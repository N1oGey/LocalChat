import os
import json
import random
import socket
import threading
import subprocess
import time

NAME_FILE = "/storage/emulated/0/name.txt"
DEFAULT_NICK = "Anonim"

GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
RESET = "\033[0m"
BOLD = "\033[1m"

print_lock = threading.Lock()


def tprint(*args, **kwargs):
    with print_lock:
        print(*args, **kwargs)


def clear_line():
    with print_lock:
        print("\033[K", end="")


def load_nick():
    try:
        with open(NAME_FILE, "r", encoding="utf-8") as f:
            nick = f.read().strip()
            return nick if nick else DEFAULT_NICK
    except Exception:
        return DEFAULT_NICK


def save_nick(nick: str):
    nick = nick.strip()[:20]
    if not nick:
        nick = DEFAULT_NICK
    try:
        with open(NAME_FILE, "w", encoding="utf-8") as f:
            f.write(nick)
        return True, nick
    except Exception as e:
        return False, str(e)


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def copy_to_clipboard(text: str):
    try:
        subprocess.run(["termux-clipboard-set", str(text)], check=False)
        return True
    except Exception:
        return False


def send_json(conn, obj):
    try:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        conn.sendall(data)
        return True
    except Exception:
        return False


def recv_json_line(file_obj):
    try:
        line = file_obj.readline()
        if not line:
            return None
        return json.loads(line)
    except Exception:
        return None


class RoomServer:
    def __init__(self, host="0.0.0.0", port=0):
        self.host = host
        self.port = port
        self.sock = None
        self.running = False
        self.accept_thread = None
        self.lock = threading.Lock()
        self.clients = {}  # conn -> {"nick":..., "role":..., "file":...}
        self.owner_conn = None
        self.owner_nick = None

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(20)
        self.sock.settimeout(0.5)
        self.running = True
        self.accept_thread = threading.Thread(target=self.accept_loop, daemon=True)
        self.accept_thread.start()
        return self.port

    def stop(self):
        self.running = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

        with self.lock:
            conns = list(self.clients.keys())

        for c in conns:
            try:
                send_json(c, {"type": "shutdown", "text": "Овнер вышел из чата."})
            except Exception:
                pass

        time.sleep(0.15)

        with self.lock:
            for c in list(self.clients.keys()):
                try:
                    c.close()
                except Exception:
                    pass
            self.clients.clear()
            self.owner_conn = None
            self.owner_nick = None

    def broadcast(self, obj, exclude=None):
        with self.lock:
            conns = list(self.clients.keys())

        for c in conns:
            if exclude is not None and c == exclude:
                continue
            send_json(c, obj)

    def current_count(self):
        with self.lock:
            return len(self.clients)

    def remove_client(self, conn, send_leave=True, owner_left=False):
        info = None
        with self.lock:
            info = self.clients.pop(conn, None)
            if self.owner_conn == conn:
                self.owner_conn = None

        if not info:
            return

        nick = info["nick"]
        count = self.current_count()

        if owner_left:
            self.broadcast({
                "type": "shutdown",
                "text": f"Овнер {nick} вышел. Чат закрывается."
            })
            self.stop()
            return

        if send_leave:
            self.broadcast({
                "type": "sys",
                "text": f"{nick} вышел из чата.",
                "count": count
            })

        try:
            conn.close()
        except Exception:
            pass

    def accept_loop(self):
        while self.running:
            try:
                conn, addr = self.sock.accept()
                conn.settimeout(None)
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
            except socket.timeout:
                continue
            except Exception:
                break

    def handle_client(self, conn, addr):
        f = conn.makefile("r", encoding="utf-8", newline="\n")
        hello = recv_json_line(f)
        if not hello or hello.get("type") != "hello":
            try:
                conn.close()
            except Exception:
                pass
            return

        nick = str(hello.get("nick", DEFAULT_NICK))[:20] or DEFAULT_NICK
        role = hello.get("role", "guest")

        with self.lock:
            self.clients[conn] = {"nick": nick, "role": role, "file": f}
            if role == "owner":
                self.owner_conn = conn
                self.owner_nick = nick

        count = self.current_count()
        self.broadcast({
            "type": "sys",
            "text": f"{nick} вошёл в чат.",
            "count": count
        })

        send_json(conn, {
            "type": "sys",
            "text": f"Ты вошёл в чат. Участников: {count}",
            "count": count
        })

        try:
            while self.running:
                msg = recv_json_line(f)
                if msg is None:
                    break

                mtype = msg.get("type")
                if mtype == "msg":
                    text = str(msg.get("text", "")).rstrip()
                    if not text:
                        continue
                    with self.lock:
                        info = self.clients.get(conn)
                    if not info:
                        continue
                    self.broadcast({
                        "type": "msg",
                        "nick": info["nick"],
                        "role": info["role"],
                        "text": text,
                        "count": self.current_count()
                    })

                elif mtype == "exit":
                    with self.lock:
                        info = self.clients.get(conn)
                    if info and info["role"] == "owner":
                        self.remove_client(conn, send_leave=False, owner_left=True)
                    else:
                        self.remove_client(conn, send_leave=True, owner_left=False)
                    break
        except Exception:
            pass

        with self.lock:
            info = self.clients.get(conn)

        if info:
            if info["role"] == "owner":
                self.remove_client(conn, send_leave=False, owner_left=True)
            else:
                self.remove_client(conn, send_leave=True, owner_left=False)


class ChatClient:
    def __init__(self, host, port, nick, role):
        self.host = host
        self.port = port
        self.nick = nick[:20] if nick else DEFAULT_NICK
        self.role = role
        self.sock = None
        self.reader = None
        self.running = False
        self.last_count = None

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        self.reader = self.sock.makefile("r", encoding="utf-8", newline="\n")
        self.running = True
        send_json(self.sock, {
            "type": "hello",
            "nick": self.nick,
            "role": self.role
        })

    def close(self):
        self.running = False
        try:
            if self.sock:
                send_json(self.sock, {"type": "exit"})
        except Exception:
            pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

    def color_nick(self, nick, role):
        if role == "owner":
            return f"{GREEN}{nick}{RESET}"
        return f"{YELLOW}{nick}{RESET}"

    def receiver(self):
        try:
            while self.running:
                data = recv_json_line(self.reader)
                if data is None:
                    break

                t = data.get("type")

                if t == "msg":
                    nick = data.get("nick", "Anonim")
                    role = data.get("role", "guest")
                    text = data.get("text", "")
                    count = data.get("count", self.last_count)
                    self.last_count = count
                    with print_lock:
                        print(f"{CYAN}[Участников: {count}]{RESET} {self.color_nick(nick, role)}: {text}")

                elif t == "sys":
                    text = data.get("text", "")
                    count = data.get("count", self.last_count)
                    self.last_count = count
                    with print_lock:
                        if count is not None:
                            print(f"{CYAN}[Участников: {count}]{RESET} {text}")
                        else:
                            print(f"{CYAN}[Система]{RESET} {text}")

                elif t == "shutdown":
                    text = data.get("text", "Чат закрыт.")
                    with print_lock:
                        print(f"{RED}{text}{RESET}")
                    self.running = False
                    break
        except Exception:
            pass
        finally:
            self.running = False

    def run(self):
        self.connect()
        thread = threading.Thread(target=self.receiver, daemon=True)
        thread.start()

        try:
            while self.running:
                try:
                    msg = input(f"{self.nick}> ").rstrip()
                except EOFError:
                    msg = "/exit"
                except KeyboardInterrupt:
                    msg = "/exit"

                if not self.running:
                    break

                if not msg:
                    continue

                if msg.lower() in ("/exit", "exit", "quit"):
                    if self.role == "owner":
                        send_json(self.sock, {"type": "exit"})
                    else:
                        send_json(self.sock, {"type": "exit"})
                    self.close()
                    break

                send_json(self.sock, {"type": "msg", "text": msg})

        finally:
            self.close()
            time.sleep(0.2)


def ask_nick():
    current = load_nick()
    print(f"Текущий ник: {BOLD}{current}{RESET}")
    nick = input("Введи новый ник (до 20 символов, Enter = Anonim): ").strip()[:20]
    if not nick:
        nick = DEFAULT_NICK

    ok, res = save_nick(nick)
    if ok:
        tprint(f"{GREEN}Ник сохранён:{RESET} {res}")
    else:
        tprint(f"{RED}Не удалось сохранить ник:{RESET} {res}")


def create_chat(my_nick):
    server = RoomServer(host="0.0.0.0", port=0)

    chosen_port = None
    for _ in range(100):
        port = random.randint(1000, 9999)
        try:
            server = RoomServer(host="0.0.0.0", port=port)
            chosen_port = server.start()
            break
        except OSError:
            continue

    if not chosen_port:
        tprint(f"{RED}Не удалось создать чат: нет свободного порта.{RESET}")
        return

    local_ip = get_local_ip()
    tprint(f"{GREEN}Чат создан!{RESET}")
    tprint(f"IP владельца: {BOLD}{local_ip}{RESET}")
    tprint(f"Порт: {BOLD}{chosen_port}{RESET}")

    if copy_to_clipboard(chosen_port):
        tprint(f"{CYAN}Порт скопирован в буфер обмена.{RESET}")
    else:
        tprint(f"{CYAN}Порт не удалось скопировать автоматически.{RESET}")

    tprint("Другим людям нужно ввести IP и порт, чтобы подключиться.")
    tprint("Для выхода из чата напиши /exit или нажми Ctrl+C.\n")

    client = ChatClient("127.0.0.1", chosen_port, my_nick, "owner")
    try:
        client.run()
    except Exception as e:
        tprint(f"{RED}Ошибка в чате:{RESET} {e}")
    finally:
        server.stop()


def join_chat(my_nick):
    host = input("Введите IP владельца чата (Enter = 127.0.0.1): ").strip()
    if not host:
        host = "127.0.0.1"

    port_txt = input("Введите порт: ").strip()
    if not port_txt.isdigit():
        tprint(f"{RED}Порт должен быть числом.{RESET}")
        return

    port = int(port_txt)
    if port < 1 or port > 65535:
        tprint(f"{RED}Неверный порт.{RESET}")
        return

    client = ChatClient(host, port, my_nick, "guest")
    try:
        client.run()
    except ConnectionRefusedError:
        tprint(f"{RED}Не удалось подключиться к чату.{RESET}")
    except Exception as e:
        tprint(f"{RED}Ошибка подключения:{RESET} {e}")


def main():
    my_nick = load_nick()

    while True:
        print("\n" + "=" * 34)
        print(f"{BOLD}Простой консольный мессенджер{RESET}")
        print(f"Твой ник: {BOLD}{my_nick}{RESET}")
        print("=" * 34)
        print("1. Поставить ник")
        print("2. Присоединиться к чату")
        print("3. Создать чат")
        print("4. Выход")

        choice = input("Выбор: ").strip()

        if choice == "1":
            ask_nick()
            my_nick = load_nick()

        elif choice == "2":
            join_chat(my_nick)
            my_nick = load_nick()

        elif choice == "3":
            create_chat(my_nick)
            my_nick = load_nick()

        elif choice == "4":
            print("Выход.")
            break

        else:
            print("Неверный выбор.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nВыход.")
