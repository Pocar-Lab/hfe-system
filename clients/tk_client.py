import asyncio, json, tkinter as tk, threading, requests
import websockets

API = "http://127.0.0.1:8000"
TOKEN = ""  # "Bearer <secret>"

def send_cmd(cmd):
    headers = {"Authorization": TOKEN} if TOKEN else {}
    r = requests.post(f"{API}/api/command", json=cmd, headers=headers, timeout=5)
    r.raise_for_status()

def on_open():  send_cmd({"id":"set_valve","open":1})
def on_close(): send_cmd({"id":"set_valve","open":0})
def on_apply():
    sp = float(entry_sp.get()); hy = float(entry_hy.get()); per = int(entry_tm.get())
    send_cmd({"id":"set_control","setpoint_C":sp,"hysteresis_C":hy,"telemetry_ms":per})

def ws_thread(lbl):
    async def run():
        async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("type")=="telemetry":
                    lbl.config(text=f"T={msg.get('tC','?')} °C | valve={msg.get('valve',0)}")
    asyncio.run(run())

root = tk.Tk(); root.title("Cryo SlowCtrl")
frame = tk.Frame(root); frame.pack(padx=12,pady=12)

tk.Label(frame,text="Setpoint (°C)").grid(row=0,column=0,sticky="e")
entry_sp = tk.Entry(frame); entry_sp.insert(0,"25.0"); entry_sp.grid(row=0,column=1)
tk.Label(frame,text="Hysteresis (°C)").grid(row=1,column=0,sticky="e")
entry_hy = tk.Entry(frame); entry_hy.insert(0,"0.5"); entry_hy.grid(row=1,column=1)
tk.Label(frame,text="Telemetry (ms)").grid(row=2,column=0,sticky="e")
entry_tm = tk.Entry(frame); entry_tm.insert(0,"1000"); entry_tm.grid(row=2,column=1)

tk.Button(frame,text="Apply",command=on_apply).grid(row=3,column=0,columnspan=2,sticky="we",pady=6)
tk.Button(frame,text="Open Valve",command=on_open).grid(row=4,column=0,sticky="we")
tk.Button(frame,text="Close Valve",command=on_close).grid(row=4,column=1,sticky="we")

lbl = tk.Label(frame,text="T=—  | valve=—"); lbl.grid(row=5,column=0,columnspan=2,pady=8)

threading.Thread(target=ws_thread, args=(lbl,), daemon=True).start()
root.mainloop()
