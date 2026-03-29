#!/usr/bin/env python3
# =============================================================================
#  user.py — Ogrenci PC Kurulum + Servis
# =============================================================================

import os, sys, shutil, subprocess, socket, struct, io
import threading, time, signal, json

KURULUM_DOSYA  = "/opt/powerconnect/user"
KURULUM_DIZIN  = "/opt/powerconnect"
SERVIS_ADI     = "powerconnect"
BROADCAST_PORT = 5559
TCP_PORT       = 5558   # Ekran yayini
DOSYA_AL_PORT  = 5557   # Dosya alma (host'tan gelir)
GEZGIN_PORT    = 5556   # Dosya gezgini

def kendi_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def ag_baglantisini_hazirla():
    """Servis baslarken arka planda ag baglantisini otomatik saglar."""
    def _arayuz_bul():
        try:
            sonuc = subprocess.run(
                ['nmcli', '-t', '-f', 'DEVICE,TYPE,STATE', 'device'],
                capture_output=True, text=True, timeout=5
            )
            for satir in sonuc.stdout.splitlines():
                parcalar = satir.split(':')
                if len(parcalar) >= 3 and parcalar[1] == 'ethernet':
                    return parcalar[0]
        except Exception:
            pass
        try:
            for ad in os.listdir('/sys/class/net'):
                if ad.startswith(('e', 'en', 'eth')):
                    return ad
        except Exception:
            pass
        return 'eth0'

    def _yap():
        for deneme in range(5):
            try:
                sonuc = subprocess.run(
                    ['nmcli', '-t', '-f', 'STATE', 'general'],
                    capture_output=True, text=True, timeout=5
                )
                if 'connected' in sonuc.stdout:
                    return
            except Exception:
                pass
            arayuz = _arayuz_bul()
            try:
                subprocess.run(
                    ['nmcli', 'device', 'connect', arayuz],
                    capture_output=True, timeout=10
                )
                time.sleep(3)
                sonuc2 = subprocess.run(
                    ['nmcli', '-t', '-f', 'STATE', 'general'],
                    capture_output=True, text=True, timeout=5
                )
                if 'connected' in sonuc2.stdout:
                    return
            except Exception:
                pass
            try:
                subprocess.run(
                    ['dhclient', '-1', arayuz],
                    capture_output=True, timeout=15
                )
                return
            except Exception:
                pass
            time.sleep(5)
    threading.Thread(target=_yap, daemon=True).start()

# =============================================================================
#  KURULUM MODU
# =============================================================================

def kurulum_yap():
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, GLib

    pencere = Gtk.Window(title="PowerConnect Kurulum")
    pencere.set_default_size(400, 200)
    pencere.set_resizable(False)
    pencere.set_position(Gtk.WindowPosition.CENTER)
    pencere.connect("destroy", Gtk.main_quit)

    kutu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    kutu.set_margin_top(24); kutu.set_margin_bottom(24)
    kutu.set_margin_start(24); kutu.set_margin_end(24)
    pencere.add(kutu)

    durum = Gtk.Label()
    durum.set_markup("<b>Kurulum yapiliyor...</b>")
    kutu.pack_start(durum, True, True, 0)

    ilerleme = Gtk.ProgressBar()
    kutu.pack_start(ilerleme, False, False, 0)

    pencere.show_all()

    def do_pulse():
        ilerleme.pulse()
        return True

    GLib.timeout_add(100, do_pulse)

    def kurulum_thread():
        hatalar = []
        try:
            os.makedirs(KURULUM_DIZIN, exist_ok=True)
            kaynak = os.path.abspath(sys.argv[0])
            if os.path.isfile(KURULUM_DOSYA):
                subprocess.run(['chattr', '-i', KURULUM_DOSYA], capture_output=True)
            shutil.copy2(kaynak, KURULUM_DOSYA)
            os.chmod(KURULUM_DOSYA, 0o755)
            subprocess.run(['chown', 'root:root', KURULUM_DOSYA], capture_output=True)
            subprocess.run(['chattr', '+i', KURULUM_DOSYA], capture_output=True)

            sudo_user = os.environ.get('SUDO_USER', '')
            if not sudo_user:
                result = subprocess.run(['who'], capture_output=True, text=True)
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if parts:
                        sudo_user = parts[0]
                        break
            if not sudo_user:
                sudo_user = 'ogrenci'

            uid = subprocess.run(['id', '-u', sudo_user], capture_output=True, text=True).stdout.strip()
            systemd_dir = f"/home/{sudo_user}/.config/systemd/user"
            os.makedirs(systemd_dir, exist_ok=True)
            subprocess.run(['chown', '-R', f'{sudo_user}:{sudo_user}',
                           f'/home/{sudo_user}/.config'], capture_output=True)

            xauth = f"/home/{sudo_user}/.Xauthority"
            if not os.path.exists(xauth):
                xauth = f"/run/user/{uid}/gdm/Xauthority"

            servis = f"""[Unit]
Description=PowerConnect Ogrenci Izleyici
After=graphical-session.target
StartLimitIntervalSec=0

[Service]
Type=simple
Environment=DISPLAY=:0
Environment=XAUTHORITY={xauth}
ExecStart={KURULUM_DOSYA}
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""
            servis_yol = f"{systemd_dir}/{SERVIS_ADI}.service"
            with open(servis_yol, 'w') as f:
                f.write(servis)

            xdg  = f"/run/user/{uid}"
            dbus = f"unix:path={xdg}/bus"

            subprocess.run(['sudo', '-u', sudo_user, 'env',
                           f'XDG_RUNTIME_DIR={xdg}', f'DBUS_SESSION_BUS_ADDRESS={dbus}',
                           'systemctl', '--user', 'daemon-reload'], capture_output=True)
            subprocess.run(['sudo', '-u', sudo_user, 'env',
                           f'XDG_RUNTIME_DIR={xdg}', f'DBUS_SESSION_BUS_ADDRESS={dbus}',
                           'systemctl', '--user', 'enable', f'{SERVIS_ADI}.service'], capture_output=True)
            subprocess.run(['loginctl', 'enable-linger', sudo_user], capture_output=True)
            subprocess.run(['sudo', '-u', sudo_user, 'env',
                           f'XDG_RUNTIME_DIR={xdg}', f'DISPLAY=:0',
                           f'XAUTHORITY={xauth}', f'DBUS_SESSION_BUS_ADDRESS={dbus}',
                           'systemctl', '--user', 'start', f'{SERVIS_ADI}.service'], capture_output=True)
        except Exception as e:
            hatalar.append(str(e))

        def guncelle():
            if hatalar:
                durum.set_markup(f'<b><span color="red">Hata: {hatalar[0]}</span></b>')
                ilerleme.set_fraction(0)
            else:
                ilerleme.set_fraction(1.0)
                durum.set_markup(
                    '<b><span color="#27ae60" size="large">✓ Kurulum Tamamlandi!</span></b>\n\n'
                    '<span color="#555">Pencereyi kapatabilirsiniz.</span>'
                )
            return False

        GLib.idle_add(guncelle)

    threading.Thread(target=kurulum_thread, daemon=True).start()
    Gtk.main()

# =============================================================================
#  SERVIS MODU
# =============================================================================

signal.signal(signal.SIGTERM, lambda s, f: None)
signal.signal(signal.SIGINT,  lambda s, f: None)

def broadcast_dongusu():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    hostname = socket.gethostname()
    while True:
        try:
            ip = kendi_ip()
            mesaj = json.dumps({"ad": hostname, "ip": ip, "port": TCP_PORT}).encode()
            for brd in ['255.255.255.255', ip.rsplit('.', 1)[0] + '.255']:
                try:
                    sock.sendto(mesaj, (brd, BROADCAST_PORT))
                except:
                    pass
        except:
            pass
        time.sleep(1)

def _tam_al(conn, n):
    veri = b''
    while len(veri) < n:
        p = conn.recv(min(65536, n - len(veri)))
        if not p:
            raise ConnectionError()
        veri += p
    return veri

# =============================================================================
#  EKRAN YAYINI
# =============================================================================

def servis_modu():
    ag_baglantisini_hazirla()  # Arka planda ag baglantisini hazirla
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, GLib, GdkPixbuf, Gdk
    from PIL import Image

    class IzlemePencere(Gtk.Window):
        def __init__(self):
            super().__init__(title="PowerConnect Client")
            self.set_decorated(False)
            self.fullscreen()
            self.set_keep_above(True)
            self.set_deletable(False)
            self.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0,0,0,1))
            self.image = Gtk.Image()
            self.add(self.image)
            self.connect("key-press-event",    lambda *a: True)
            self.connect("key-release-event",  lambda *a: True)
            self.connect("button-press-event", lambda *a: True)
            self.connect("delete-event",       lambda *a: True)
            # Pencere ve gorev cubugu ikonu
            try:
                ikon_yollari = [
                    '/usr/share/pixmaps/powerconnect-client.png',
                    '/usr/share/icons/hicolor/256x256/apps/powerconnect-client.png',
                    '/usr/share/pixmaps/powerconnect.png',
                    '/usr/share/icons/hicolor/256x256/apps/powerconnect.png',
                ]
                for yol in ikon_yollari:
                    if os.path.exists(yol):
                        pb = GdkPixbuf.Pixbuf.new_from_file(yol)
                        self.set_icon(pb)
                        break
                else:
                    self.set_icon_name('powerconnect-client')
            except Exception:
                pass
            self.show_all()
            self.hide()
            self._pencereli_mod = False

        def kare_goster(self, veri):
            try:
                img = Image.open(io.BytesIO(veri)).convert('RGB')
                # Pencere boyutuna gore scale et (pencereli modda resize destegi)
                alloc = self.get_allocation()
                pen_w = alloc.width  if alloc.width  > 1 else self.get_screen().get_width()
                pen_h = alloc.height if alloc.height > 1 else self.get_screen().get_height()
                img.thumbnail((pen_w, pen_h), Image.LANCZOS)
                w, h = img.size
                raw = img.tobytes()
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_bytes(
                        GLib.Bytes.new(raw), GdkPixbuf.Colorspace.RGB, False, 8, w, h, w*3)
                except AttributeError:
                    pb = GdkPixbuf.Pixbuf.new_from_data(
                        raw, GdkPixbuf.Colorspace.RGB, False, 8, w, h, w*3)
                    pb._r = raw
                self.image.set_from_pixbuf(pb)
            except:
                pass

        def ac(self, pencereli=False):
            self._pencereli_mod = pencereli
            if pencereli:
                # Pencereli mod: baslik cubugu var, alt+tab/minimize serbest
                # Boyutlandirma ve kapat (X) engelleniyor
                self.set_title("PowerConnect — Ogretmen Ekrani")
                self.set_decorated(True)
                self.set_resizable(True)
                self.set_keep_above(False)
                self.set_deletable(False)   # X butonu calismiyor
                self.unfullscreen()
                self.resize(800, 600)
                try:
                    self.disconnect_by_func(self._engelle)
                except Exception:
                    pass
            else:
                # Penceresiz mod: tam ekran, her sey kilitli
                self.set_decorated(False)
                self.set_keep_above(True)
                self.set_deletable(False)
                self.fullscreen()
            self.show_all()
            self.present()

        def kapat_ekran(self):
            self.hide()
            # Bir sonraki baglanti icin sifirla
            self.set_decorated(False)
            self.set_keep_above(True)
            self.set_deletable(False)

    def ekran_baglanti_isle(conn, pencere):
        # Ilk byte mod bilgisi: b'W' = pencereli, b'F' = fullscreen
        try:
            # Keepalive: baglanti sessizce kopunca anlasin
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            conn.settimeout(30)
            mod_byte = conn.recv(1)
            pencereli = (mod_byte == b'W')
        except:
            pencereli = False
        GLib.idle_add(pencere.ac, pencereli)
        try:
            while True:
                boyut = struct.unpack('>I', _tam_al(conn, 4))[0]
                if boyut == 0xFFFFFFFF:
                    break
                if boyut == 0:
                    continue
                veri = _tam_al(conn, boyut)
                GLib.idle_add(pencere.kare_goster, veri)
        except:
            pass
        finally:
            try: conn.close()
            except: pass
            GLib.idle_add(pencere.kapat_ekran)

    def ekran_sunucu(pencere):
        while True:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(('0.0.0.0', TCP_PORT))
                s.listen(1)
                while True:
                    conn, addr = s.accept()
                    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    threading.Thread(target=ekran_baglanti_isle, args=(conn, pencere), daemon=True).start()
            except:
                time.sleep(3)

    # =============================================================================
    #  DOSYA ALMA (host'tan gelir, masaüstüne kaydeder)
    # =============================================================================

    def dosya_al_sunucu():
        while True:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(('0.0.0.0', DOSYA_AL_PORT))
                s.listen(5)
                while True:
                    conn, addr = s.accept()
                    threading.Thread(target=dosya_al_isle, args=(conn,), daemon=True).start()
            except:
                time.sleep(3)

    def dosya_al_isle(conn):
        try:
            # Ad uzunlugu (goreli yol olabilir: "klasor/dosya.txt")
            ad_len = struct.unpack('>I', _tam_al(conn, 4))[0]
            dosya_adi = _tam_al(conn, ad_len).decode()
            # Dosya boyutu
            dosya_len = struct.unpack('>I', _tam_al(conn, 4))[0]
            dosya_veri = _tam_al(conn, dosya_len)
            # Masaüstüne kaydet (alt klasorler otomatik olusturulur)
            masaustu = os.path.expanduser('~/Masaüstü')
            if not os.path.exists(masaustu):
                masaustu = os.path.expanduser('~/Desktop')
            hedef = os.path.join(masaustu, dosya_adi)
            # Alt klasor varsa olustur
            os.makedirs(os.path.dirname(hedef), exist_ok=True)
            with open(hedef, 'wb') as f:
                f.write(dosya_veri)
        except:
            pass
        finally:
            try: conn.close()
            except: pass

    # =============================================================================
    #  DOSYA GEZGINI (host'a dosya listesi ve dosya gonderir)
    # =============================================================================

    def gezgin_sunucu():
        while True:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(('0.0.0.0', GEZGIN_PORT))
                s.listen(5)
                while True:
                    conn, addr = s.accept()
                    threading.Thread(target=gezgin_isle, args=(conn,), daemon=True).start()
            except:
                time.sleep(3)

    def gezgin_isle(conn):
        try:
            while True:
                # Komut al
                komut_len = struct.unpack('>I', _tam_al(conn, 4))[0]
                komut_veri = json.loads(_tam_al(conn, komut_len).decode())
                komut = komut_veri.get('komut')

                if komut == 'listele':
                    yol = komut_veri.get('yol', os.path.expanduser('~'))
                    try:
                        girişler = []
                        for isim in sorted(os.listdir(yol)):
                            tam_yol = os.path.join(yol, isim)
                            try:
                                stat = os.stat(tam_yol)
                                girişler.append({
                                    'isim': isim,
                                    'yol': tam_yol,
                                    'dizin': os.path.isdir(tam_yol),
                                    'boyut': stat.st_size
                                })
                            except:
                                pass
                        yanit = json.dumps({'durum': 'ok', 'girişler': girişler, 'yol': yol}).encode()
                    except Exception as e:
                        yanit = json.dumps({'durum': 'hata', 'mesaj': str(e)}).encode()
                    conn.sendall(struct.pack('>I', len(yanit)) + yanit)

                elif komut == 'indir':
                    yol = komut_veri.get('yol')
                    try:
                        with open(yol, 'rb') as f:
                            veri = f.read()
                        dosya_adi = os.path.basename(yol).encode()
                        yanit_meta = json.dumps({'durum': 'ok', 'isim': os.path.basename(yol), 'boyut': len(veri)}).encode()
                        conn.sendall(struct.pack('>I', len(yanit_meta)) + yanit_meta)
                        conn.sendall(struct.pack('>I', len(veri)) + veri)
                    except Exception as e:
                        yanit = json.dumps({'durum': 'hata', 'mesaj': str(e)}).encode()
                        conn.sendall(struct.pack('>I', len(yanit)) + yanit)

                elif komut == 'kapat':
                    break
        except:
            pass
        finally:
            try: conn.close()
            except: pass

    pencere = IzlemePencere()
    threading.Thread(target=broadcast_dongusu, daemon=True).start()
    threading.Thread(target=ekran_sunucu, args=(pencere,), daemon=True).start()
    threading.Thread(target=dosya_al_sunucu, daemon=True).start()
    threading.Thread(target=gezgin_sunucu, daemon=True).start()
    Gtk.main()

# =============================================================================
#  BASLAT
# =============================================================================

if __name__ == '__main__':
    # /opt/powerconnect altinda calisiyorsa servis modu
    exe_path = os.path.abspath(sys.argv[0])
    if exe_path.startswith('/opt/powerconnect'):
        servis_modu()
    else:
        if os.geteuid() != 0:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk
            dialog = Gtk.MessageDialog(
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Root yetkisi gerekli!\n\nsudo ./user komutuyla calistirin."
            )
            dialog.run()
            dialog.destroy()
            sys.exit(1)
        kurulum_yap()
