import os
import json
import random
import socket
import threading
import subprocess
import time

NAME_FILE = "/storage/emulated/0/name.txt"
DEFAULT_NICK = "echat_user"

BANNER = r"""
▓█████  ▄████▄   ██░ ██  ▄▄▄     ▄▄▄█████▓
▓█   ▀ ▒██▀ ▀█  ▓██░ ██▒▒████▄   ▓  ██▒ ▓▒
▒███   ▒▓█    ▄ ▒██▀▀██░▒██  ▀█▄ ▒ ▓██░ ▒░
▒▓█  ▄ ▒▓▓▄ ▄██▒░▓█ ░██ ░██▄▄▄▄██░ ▓██▓ ░ 
░▒████▒▒ ▓███▀ ░░▓█▒░██▓ ▓█   ▓██▒ ▒██▒ ░ 
░░ ▒░ ░░ ░▒ ▒  ░ ▒ ░░▒░▒ ▒▒   ▓▒█░ ▒ ░░   
 ░ ░  ░  ░  ▒    ▒ ░▒░ ░  ▒   ▒▒ ░   ░    
   ░   ░         ░  ░░ ░  ░   ▒    ░      
   ░  ░░ ░       ░  ░  ░      ░  ░        
       ░                                  
"""

GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
RESET = "\033[0m"
BOLD = "\033[1m"

print_lock = threading.Lock()


def clear_screen():
    os.system("clear")


def tprint(*args, **kwargs):
    with print_lock:
        print(*args, **kwargs)


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
        self.clients = {}
        self.owner_conn = None

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
                send_json(c, {"type": "shutdown", "text": "The owner left. The chat is closing."})
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

    def current_count(self):
        with self.lock:
            return len(self.clients)

    def broadcast(self, obj, exclude=None):
        with self.lock:
            conns = list(self.clients.keys())

        for c in conns:
            if exclude is not None and c == exclude:
                continue
            send_json(c, obj)

    def remove_client(self, conn, send_leave=True, owner_left=False):
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
                "text": f"The owner {nick} left. The chat is closing."
            })
            self.stop()
            return

        if send_leave:
            self.broadcast({
                "type": "sys",
                "text": f"{nick} left the chat.",
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

        count = self.current_count()
        self.broadcast({
            "type": "sys",
            "text": f"{nick} joined the chat.",
            "count": count
        })

        send_json(conn, {
            "type": "sys",
            "text": f"You joined the chat. Users online: {count}",
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
                    }, exclude=conn)

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
                        print(f"{CYAN}[Users online: {count}]{RESET} {self.color_nick(nick, role)}: {text}")

                elif t == "sys":
                    text = data.get("text", "")
                    count = data.get("count", self.last_count)
                    self.last_count = count
                    with print_lock:
                        if count is not None:
                            print(f"{CYAN}[Users online: {count}]{RESET} {text}")
                        else:
                            print(f"{CYAN}[System]{RESET} {text}")

                elif t == "shutdown":
                    text = data.get("text", "Chat closed.")
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
                    send_json(self.sock, {"type": "exit"})
                    self.close()
                    break

                send_json(self.sock, {"type": "msg", "text": msg})

        finally:
            self.close()
            time.sleep(0.2)


def pause(msg="Press Enter to continue..."):
    try:
        input(msg)
    except KeyboardInterrupt:
        pass


def ask_nick():
    clear_screen()
    print(BANNER)
    current = load_nick()
    print(f"Current nick: {BOLD}{current}{RESET}")
    nick = input("Enter a new nick (max 20 chars, Enter = nothing): ").strip()[:20]
    if not nick:
        nick = DEFAULT_NICK

    ok, res = save_nick(nick)
    if ok:
        print(f"{GREEN}Nick saved:{RESET} {res}")
    else:
        print(f"{RED}Failed to save nick:{RESET} {res}")
    pause()


def create_chat(my_nick):
    clear_screen()
    print(BANNER)

    server = None
    chosen_port = None

    for _ in range(100):
        port = random.randint(1000, 9999)
        try:
            server = RoomServer(host="0.0.0.0", port=port)
            chosen_port = server.start()
            break
        except OSError:
            continue

    if not server or not chosen_port:
        print(f"{RED}Failed to create a chat. No free port was available.{RESET}")
        pause()
        return

    local_ip = get_local_ip()

    print(f"{GREEN}Chat created!{RESET}")
    print(f"Your IP: {BOLD}{local_ip}{RESET}")
    print(f"Port: {BOLD}{chosen_port}{RESET}")

    if copy_to_clipboard(str(chosen_port)):
        print(f"{CYAN}Port copied to clipboard.{RESET}")

    print("Other users need your IP address and port to connect.")
    print("Type /exit to close the chat.\n")

    client = ChatClient("127.0.0.1", chosen_port, my_nick, "owner")
    try:
        client.run()
    except Exception as e:
        print(f"{RED}Chat error:{RESET} {e}")
    finally:
        server.stop()
        clear_screen()
        pause("Chat closed. Press Enter to return to the menu...")


def join_chat(my_nick):
    clear_screen()
    print(BANNER)

    host = input("Enter the owner's IP address (Enter = 127.0.0.1): ").strip()
    if not host:
        host = "127.0.0.1"

    port_txt = input("Enter the port: ").strip()
    if not port_txt.isdigit():
        print(f"{RED}Port must be a number.{RESET}")
        pause()
        return

    port = int(port_txt)
    if port < 1 or port > 65535:
        print(f"{RED}Invalid port.{RESET}")
        pause()
        return

    client = ChatClient(host, port, my_nick, "guest")
    try:
        client.run()
    except ConnectionRefusedError:
        print(f"{RED}Could not connect to the chat.{RESET}")
        pause()
    except Exception as e:
        print(f"{RED}Connection error:{RESET} {e}")
        pause()


def main():
    clear_screen()
    my_nick = load_nick()

    while True:
        clear_screen()
        print(BANNER)
        print(f"{BOLD}𝐄𝐜𝐡𝐚𝐭 v1 by @KrystalArial{RESET}")
        print("=" * 36)
        print(f"Your nick: {BOLD}{my_nick}{RESET}\n")
        print("1. Set nick")
        print("2. Join chat")
        print("3. Create chat")
        print("4. Exit\n")
        print("=" * 36)

        choice = input("Choose 1-4: ").strip()

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
            clear_screen()
            print("Goodbye.")
            break

        else:
            print("Invalid choice. Please enter 1, 2, 3, or 4.")
            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        clear_screen()
        print("Goodbye.")