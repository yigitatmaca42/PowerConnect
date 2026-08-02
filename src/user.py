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
TCP_PORT       = 5558   # Ekran yayını
DOSYA_AL_PORT  = 5557   # Dosya alma (host'tan gelir)
GEZGIN_PORT    = 5556   # Dosya gezgini
IZLE_PORT      = 5555   # Ekran izleme (host'a gönderir)

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

def _kurulum_islemleri():
    """
    Asıl kurulum işlemleri: binary'yi /opt/powerconnect'e kopyalar,
    chattr +i ile kilitler, systemd --user servisini oluşturup başlatır.
    Hem GUI kurulumdan (kurulum_yap) hem de .deb postinst'ten
    (--headless-install) çağrılır.
    Hata olursa exception fırlatır, başarılıysa None döner.
    """
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
        # /home altındaki ilk gerçek kullanıcıyı dene (headless .deb kurulumunda
        # 'who' boş dönebilir çünkü henüz oturum açılmamış olabilir)
        for ad in sorted(os.listdir('/home')):
            if os.path.isdir(f'/home/{ad}'):
                sudo_user = ad
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
    subprocess.run(['chown', f'{sudo_user}:{sudo_user}', servis_yol], capture_output=True)

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


def kurulum_yap_headless():
    """.deb postinst tarafından çağrılır — GUI açmadan kurulum yapar."""
    try:
        _kurulum_islemleri()
        print("✓ PowerConnect İstemci kuruldu ve servis başlatıldı.")
    except Exception:
        print("✗ Kurulum hatası: İşlem tamamlanamadı")
        sys.exit(1)


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
            _kurulum_islemleri()
        except Exception:
            hatalar.append("İşlem tamamlanamadı")

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

MAX_PAKET_BOYUTU = 10 * 1024 * 1024  # 10 MB

def _tam_al(conn, n):
    if n > MAX_PAKET_BOYUTU:
        raise ValueError(f"Paket çok büyük: {n}")
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
            self._isleniyor_lock = threading.Lock()
            self._isleniyor = False

        def kare_goster_worker(self, veri):
            """
            Network thread'den çağrılır. JPEG decode (pahalı, GTK'sız) burada.
            Boyut hesabı (get_allocation/get_screen) GTK çağrısıdır — main
            thread'e devredilir, worker thread'de YAPILMAZ.
            """
            with self._isleniyor_lock:
                if self._isleniyor:
                    return  # Önceki kare hâlâ işleniyor — bu kareyi atla
                self._isleniyor = True
            try:
                img = Image.open(io.BytesIO(veri)).convert('RGB')
                # Boyutlandırma + GTK ataması main thread'de yapılacak
                GLib.idle_add(self._kare_isle_main_thread, img)
            except:
                with self._isleniyor_lock:
                    self._isleniyor = False

        def _kare_isle_main_thread(self, img):
            """SADECE main thread'den çağrılır — get_allocation/get_screen güvenli."""
            try:
                # Ekran çözünürlüğünü al
                ekran_w = self.get_screen().get_width()
                ekran_h = self.get_screen().get_height()
                # Her zaman tam ekran boyutuna ölçekle — pencere küçük olsa bile
                # Öğrenci her zaman 1920x1080 (veya kendi monitörü) görür
                img = img.resize((ekran_w, ekran_h), Image.BILINEAR)
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
            with self._isleniyor_lock:
                self._isleniyor = False
            return False

        def kare_goster(self, veri):
            """Geriye dönük uyumluluk için — artık worker'a yönlendirir."""
            self.kare_goster_worker(veri)

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

    _ekran_aktif_lock = threading.Lock()
    _ekran_aktif_conn = [None]  # mutable container - tek aktif bağlantıyı izler

    def ekran_baglanti_isle(conn, pencere):
        # Aynı anda yalnızca 1 host bağlantısı kabul edilir — birden fazla
        # thread'in aynı pencere nesnesine eş zamanlı erişip kare/durum
        # karışıklığına (race condition) yol açmasını engeller.
        with _ekran_aktif_lock:
            if _ekran_aktif_conn[0] is not None:
                try: conn.close()
                except: pass
                return
            _ekran_aktif_conn[0] = conn

        try:
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
                    pencere.kare_goster_worker(veri)
            except:
                pass
        finally:
            try: conn.close()
            except: pass
            with _ekran_aktif_lock:
                if _ekran_aktif_conn[0] is conn:
                    _ekran_aktif_conn[0] = None
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

    def _masaustu_bul():
        """Bug #3 fix: Büyük/küçük harf ve farklı isim varyantlarını dener."""
        adaylar = ['~/Masaüstü', '~/masaüstü', '~/Desktop', '~/desktop', '~']
        for aday in adaylar:
            yol = os.path.expanduser(aday)
            if os.path.isdir(yol):
                return yol
        return os.path.expanduser('~')

    def dosya_al_isle(conn):
        try:
            ad_len = struct.unpack('>I', _tam_al(conn, 4))[0]
            if ad_len > 4096:
                return  # Çok uzun dosya adı — reddet
            dosya_adi = _tam_al(conn, ad_len).decode()
            dosya_len = struct.unpack('>I', _tam_al(conn, 4))[0]
            masaustu = _masaustu_bul()
            hedef = os.path.realpath(os.path.join(masaustu, dosya_adi))
            masaustu_gercek = os.path.realpath(masaustu)
            # Path traversal koruması: hedef masaüstü altında mı?
            if not hedef.startswith(masaustu_gercek + os.sep) and hedef != masaustu_gercek:
                return
            hedef_dir = os.path.dirname(hedef)
            if hedef_dir and hedef_dir != hedef:
                os.makedirs(hedef_dir, exist_ok=True)
            kalan = dosya_len
            with open(hedef, 'wb') as f:
                while kalan > 0:
                    chunk = conn.recv(min(256 * 1024, kalan))
                    if not chunk:
                        raise ConnectionError("Bağlantı kesildi")
                    f.write(chunk)
                    kalan -= len(chunk)
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
                    except Exception:
                        yanit = json.dumps({'durum': 'hata', 'mesaj': 'Dizin okunamadı'}).encode()
                    conn.sendall(struct.pack('>I', len(yanit)) + yanit)

                elif komut == 'indir':
                    yol = komut_veri.get('yol')
                    try:
                        # Bug #1 fix: Dosya boyutunu önceden belirle, chunk'lı gönder
                        dosya_boyutu = os.path.getsize(yol)
                        yanit_meta = json.dumps({'durum': 'ok', 'isim': os.path.basename(yol), 'boyut': dosya_boyutu}).encode()
                        conn.sendall(struct.pack('>I', len(yanit_meta)) + yanit_meta)
                        conn.sendall(struct.pack('>I', dosya_boyutu))
                        with open(yol, 'rb') as f:
                            while True:
                                chunk = f.read(256 * 1024)
                                if not chunk:
                                    break
                                conn.sendall(chunk)
                    except Exception:
                        yanit = json.dumps({'durum': 'hata', 'mesaj': 'Dosya okunamadı'}).encode()
                        conn.sendall(struct.pack('>I', len(yanit)) + yanit)

                elif komut == 'kapat':
                    break
        except:
            pass
        finally:
            try: conn.close()
            except: pass

    # =============================================================================
    #  ÖZELLİK 1: EKRAN İZLEME SUNUCUSU (host'a ekran gönderir)
    # =============================================================================

    def izle_sunucu():
        while True:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(('0.0.0.0', IZLE_PORT))
                s.listen(5)
                while True:
                    conn, addr = s.accept()
                    threading.Thread(target=izle_isle, args=(conn,), daemon=True).start()
            except Exception:
                time.sleep(3)

    def izle_isle(conn):
        try:
            import mss
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            # İlk mesaj: mod bilgisi {'mod': 'izleme'} veya {'mod': 'kontrol'}
            mod_len_b = _tam_al(conn, 4)
            mod_len = struct.unpack('>I', mod_len_b)[0]
            mod_veri = json.loads(_tam_al(conn, mod_len).decode())
            mod = mod_veri.get('mod', 'izleme')

            if mod == 'kontrol':
                # Kontrol modu: sadece komut alır, kare göndermez.
                # KRİTİK PERFORMANS: Her komut için ayrı xdotool process'i
                # başlatmak (subprocess.run) saniyede ~30 kez process
                # spawn etmek demekti — bu ciddi gecikmeye ve komutların
                # sıraya girip geç işlenmesine sebep oluyordu ("fare
                # aşağıda görünüyor ama yukarı tıklıyor" hissi buradan
                # geliyordu). Tek bir xdotool process'i kalıcı olarak
                # açılır, komutlar stdin üzerinden satır satır beslenir.
                xdo_proc = None
                try:
                    xdo_proc = subprocess.Popen(
                        ['xdotool', '-'],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        bufsize=0,  # satır satır anında flush
                        text=True
                    )
                except Exception:
                    xdo_proc = None

                def _xdo_gonder(satir):
                    """Kalıcı xdotool process'ine tek satır komut yazar."""
                    if xdo_proc is None or xdo_proc.poll() is not None:
                        return
                    try:
                        xdo_proc.stdin.write(satir + "\n")
                        xdo_proc.stdin.flush()
                    except Exception:
                        pass

                def komut_al_dongusu():
                    while True:
                        try:
                            k_len_b = b''
                            while len(k_len_b) < 4:
                                p = conn.recv(4 - len(k_len_b))
                                if not p:
                                    return
                                k_len_b += p
                            k_len = struct.unpack('>I', k_len_b)[0]
                            if k_len == 0xFFFFFFFF or k_len == 0:
                                continue
                            komut = json.loads(_tam_al(conn, k_len).decode())
                            tip = komut.get('tip', '')
                            if tip == 'fare_hareket':
                                _xdo_gonder(f"mousemove {int(komut['x'])} {int(komut['y'])}")
                            elif tip == 'fare_bas':
                                _xdo_gonder(f"mousemove {int(komut['x'])} {int(komut['y'])}")
                                _xdo_gonder(f"mousedown {int(komut.get('tus', 1))}")
                            elif tip == 'fare_birak':
                                _xdo_gonder(f"mouseup {int(komut.get('tus', 1))}")
                            elif tip == 'klavye':
                                keyname = komut.get('keyname', '')
                                if keyname and all(c.isalnum() or c in '_+-' for c in keyname):
                                    _xdo_gonder(f"key {keyname}")
                            elif tip == 'scroll':
                                btn = '5' if komut.get('yon') == 'asagi' else '4'
                                _xdo_gonder(f"click {btn}")
                        except Exception:
                            break

                try:
                    komut_al_dongusu()
                finally:
                    if xdo_proc is not None:
                        try:
                            xdo_proc.stdin.close()
                        except Exception:
                            pass
                        try:
                            xdo_proc.terminate()
                        except Exception:
                            pass
                return  # Kontrol modu bitti

            # Önizleme modu: dengeli kalite — GTK main loop'u boğmayacak hız
            aralik = 1.0 / 24  # 24 FPS — host.py IZLE_FPS ile uyumlu
            with mss.mss() as sct:
                ekran = sct.monitors[1]
                while True:
                    t0 = time.time()
                    goruntu = sct.grab(ekran)
                    img = Image.frombytes("RGB", goruntu.size, goruntu.bgra, "raw", "BGRX")
                    # 1600x900 — ağ ve CPU dengesi
                    if img.width > 1600 or img.height > 900:
                        img.thumbnail((1600, 900), Image.BILINEAR)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=80, optimize=False)
                    veri = buf.getvalue()
                    conn.settimeout(10)
                    conn.sendall(struct.pack('>I', len(veri)) + veri)
                    conn.settimeout(None)
                    gecen = time.time() - t0
                    bekle = aralik - gecen
                    if bekle > 0:
                        time.sleep(bekle)
        except:
            pass
        finally:
            try:
                conn.sendall(struct.pack('>I', 0xFFFFFFFF))
                conn.close()
            except: pass


    pencere = IzlemePencere()
    threading.Thread(target=broadcast_dongusu, daemon=True).start()
    threading.Thread(target=ekran_sunucu, args=(pencere,), daemon=True).start()
    threading.Thread(target=dosya_al_sunucu, daemon=True).start()
    threading.Thread(target=gezgin_sunucu, daemon=True).start()
    threading.Thread(target=izle_sunucu, daemon=True).start()
    Gtk.main()

# =============================================================================
#  BASLAT
# =============================================================================

if __name__ == '__main__':
    # .deb postinst tarafından çağrılır: GUI açmadan kurulum yapar
    if '--headless-install' in sys.argv:
        if os.geteuid() != 0:
            print("✗ Root yetkisi gerekli (postinst root olarak çalışmalı).")
            sys.exit(1)
        kurulum_yap_headless()
        sys.exit(0)

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
