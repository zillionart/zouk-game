import qrcode

qr = qrcode.make("http://<your-hostname-or-ip>:<port>/join")
qr.save("static/qrcode.png")
