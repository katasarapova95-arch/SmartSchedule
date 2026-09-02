from pathlib import Path
import socket, threading, webbrowser, os
from app import app

def free_port(start=8891, end=8999):
    requested=os.environ.get("PORT")
    if requested:
        return int(requested)
    for port in range(start,end+1):
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1",port))
                return port
            except OSError:
                pass
    raise RuntimeError("Не найден свободный порт")

PORT=free_port()

def lan_ip():
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1",80)); return s.getsockname()[0]
    except Exception:
        return "IP_КОМПЬЮТЕРА"
    finally:s.close()

print("="*68)
print("SMARTSCHEDULE · СТАБИЛЬНЫЙ ЛОКАЛЬНЫЙ СЕРВЕР")
print("Компьютер: http://127.0.0.1:%s" % PORT)
print("Телефон в той же Wi-Fi: http://%s:%s" % (lan_ip(),PORT))
print("Если открылся старый порт, это не проблема: эта версия использует новый свободный порт.")
print("Не закрывай это окно во время работы программы.")
print("="*68)
threading.Timer(.8,lambda:webbrowser.open("http://127.0.0.1:%s/login"%PORT)).start()
app.run(host="0.0.0.0",port=PORT,debug=False,use_reloader=False)
