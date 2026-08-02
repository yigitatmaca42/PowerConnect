#!/usr/bin/env python3
# =============================================================================
#  host.py — Yönetici Paneli
# =============================================================================

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk

import socket, threading, struct, io, json, time, os, subprocess, sys
import mss
from PIL import Image

def _varlik_yolu(dosya_adi):
    """
    ELF (PyInstaller) içindeyse sys._MEIPASS/assets/,
    değilse önce /usr/share/pixmaps/, sonra script yanı assets/ dener.
    """
    adaylar = []
    # 1) PyInstaller bundle içi
    if hasattr(sys, '_MEIPASS'):
        adaylar.append(os.path.join(sys._MEIPASS, 'assets', dosya_adi))
    # 2) Sistem pixmaps (kurulu paket)
    adaylar.append(os.path.join('/usr/share/pixmaps', dosya_adi))
    # 3) Script'in yanındaki assets/ klasörü (geliştirme)
    adaylar.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', dosya_adi))
    for yol in adaylar:
        if os.path.isfile(yol):
            return yol
    return None

def ag_baglantisini_hazirla():
    """Uygulama acilinca arka planda ag baglantisini bir kez dener.
    Tum ethernet arayuzleri bulunur, nmcli / dhcpcd / dhclient / udhcpc
    sirayla denenir; biri basarili olur olmaz durur."""

    def _arayuzleri_bul():
        arayuzler = []
        try:
            sonuc = subprocess.run(
                ['nmcli', '-t', '-f', 'DEVICE,TYPE,STATE', 'device'],
                capture_output=True, text=True, timeout=5
            )
            for satir in sonuc.stdout.splitlines():
                p = satir.split(':')
                if len(p) >= 3 and p[1] == 'ethernet':
                    arayuzler.append(p[0])
        except Exception:
            pass
        try:
            for ad in sorted(os.listdir('/sys/class/net')):
                if ad.startswith(('eth', 'en', 'enp', 'ens', 'enx')) and ad not in arayuzler:
                    arayuzler.append(ad)
        except Exception:
            pass
        return arayuzler or ['eth0', 'enp0s3']

    def _bagli_mi():
        try:
            r = subprocess.run(['nmcli', '-t', '-f', 'STATE', 'general'],
                               capture_output=True, text=True, timeout=5)
            if 'connected' in r.stdout:
                return True
        except Exception:
            pass
        try:
            r = subprocess.run(['ip', 'addr', 'show'],
                               capture_output=True, text=True, timeout=5)
            for satir in r.stdout.splitlines():
                if satir.strip().startswith('inet ') and '127.0.0.1' not in satir:
                    return True
        except Exception:
            pass
        return False

    def _yap():
        if _bagli_mi():
            return  # Zaten bagli, hic bir sey yapma

        for arayuz in _arayuzleri_bul():
            try:
                subprocess.run(['ip', 'link', 'set', arayuz, 'up'],
                               capture_output=True, timeout=5)
            except Exception:
                pass

            try:
                subprocess.run(['nmcli', 'device', 'connect', arayuz],
                               capture_output=True, timeout=10)
                time.sleep(2)
                if _bagli_mi(): return
            except Exception:
                pass

            try:
                subprocess.run(['nmcli', 'connection', 'up', 'ifname', arayuz],
                               capture_output=True, timeout=10)
                time.sleep(2)
                if _bagli_mi(): return
            except Exception:
                pass

            try:
                subprocess.run(['dhcpcd', arayuz], capture_output=True, timeout=15)
                time.sleep(2)
                if _bagli_mi(): return
            except Exception:
                pass

            try:
                subprocess.run(['dhclient', '-1', arayuz],
                               capture_output=True, timeout=15)
                time.sleep(2)
                if _bagli_mi(): return
            except Exception:
                pass

            try:
                subprocess.run(['udhcpc', '-i', arayuz, '-q'],
                               capture_output=True, timeout=15)
                time.sleep(2)
                if _bagli_mi(): return
            except Exception:
                pass

    threading.Thread(target=_yap, daemon=True).start()

BROADCAST_PORT   = 5559
FPS              = 25      # Yayın FPS — 60 GTK main loop'u tıkıyordu
QUALITY          = 65
SCALE            = 1.0
DOSYA_AL_PORT    = 5557
GEZGIN_PORT      = 5556
IZLE_PORT        = 5555

IZLE_MAX_W       = 1600    # Büyük izleme penceresi maks genişlik
IZLE_MAX_H       = 900     # Büyük izleme penceresi maks yükseklik
ONIZLEME_FPS     = 12      # Küçük kart önizleme FPS

baglantilar      = {}
baglantilar_lock = threading.Lock()
son_gorunme      = {}
son_gorunme_lock = threading.Lock()

# Öğrenci ekranı izleme kareleri: ip → bytes (JPEG)
onizleme_kareleri     = {}
onizleme_kareleri_lock = threading.Lock()

# Her IP için açık izleme penceresi takibi — çoklu pencere engeller
acik_izleme_pencereleri     = {}
acik_izleme_pencereleri_lock = threading.Lock()

def kendi_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def kendi_ipler():
    """Bu makinenin sahip olduğu tüm IPv4 adreslerini döner (loopback hariç)."""
    ipler = set()
    try:
        ipler.add(kendi_ip())
    except:
        pass
    try:
        sonuc = subprocess.run(['ip', '-4', 'addr', 'show'],
                               capture_output=True, text=True, timeout=5)
        for satir in sonuc.stdout.splitlines():
            satir = satir.strip()
            if satir.startswith('inet '):
                ip = satir.split()[1].split('/')[0]
                if ip != '127.0.0.1':
                    ipler.add(ip)
    except:
        pass
    return ipler

def broadcast_dinle(pencere):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', BROADCAST_PORT))
    while True:
        try:
            veri, _ = sock.recvfrom(1024)
            bilgi = json.loads(veri.decode())
            ad = bilgi.get("ad", "").strip()
            ip = bilgi.get("ip", "").strip()
            # IP formatını doğrula — geçersiz IP'leri reddet
            if not _ip_gecerli(ip):
                continue
            # Ad güvenli hale getir — markup injection'a karşı
            ad = _guvenli_metin(ad) if ad else ip
            if ad and ip and ip not in kendi_ipler():
                with son_gorunme_lock:
                    son_gorunme[ip] = time.time()
                GLib.idle_add(pencere.pc_guncelle, ad, ip)
        except:
            pass

def kopuk_kontrol(pencere):
    """Her 3 saniyede bir kopuk PC leri listeden siler."""
    while True:
        time.sleep(3)
        simdi = time.time()
        with son_gorunme_lock:
            kopuklar = [ip for ip, t in son_gorunme.items() if simdi - t > 5]
        for ip in kopuklar:
            with son_gorunme_lock:
                son_gorunme.pop(ip, None)
            with baglantilar_lock:
                bilgi = baglantilar.get(ip)
                if bilgi:
                    bilgi['aktif'] = False
                    try:
                        bilgi['conn'].close()
                    except:
                        pass
                    del baglantilar[ip]
            GLib.idle_add(pencere.pc_kaldir, ip)

MAX_PAKET_BOYUTU = 10 * 1024 * 1024  # 10 MB — DoS koruması

def _tam_al(conn, n):
    if n > MAX_PAKET_BOYUTU:
        raise ValueError(f"Paket boyutu çok büyük: {n}")
    veri = b''
    while len(veri) < n:
        p = conn.recv(min(65536, n - len(veri)))
        if not p:
            raise ConnectionError()
        veri += p
    return veri

def _ip_gecerli(ip):
    """IPv4 formatını doğrular."""
    try:
        parcalar = ip.split('.')
        if len(parcalar) != 4:
            return False
        return all(0 <= int(p) <= 255 for p in parcalar)
    except Exception:
        return False

def _guvenli_metin(metin):
    """GTK markup injection'a karşı özel karakterleri temizler."""
    return str(metin).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')[:64]

YAYIN_SEND_TIMEOUT = 10  # Bug #2 fix: Ağ donarsa max 10 sn bekle

def yayin_dongusu(ip, pencere):
    aralik = 1.0 / FPS
    with mss.mss() as sct:
        ekran = sct.monitors[1]
        while True:
            with baglantilar_lock:
                bilgi = baglantilar.get(ip)
                if not bilgi or not bilgi['aktif']:
                    break
                conn = bilgi['conn']
            t0 = time.time()
            try:
                goruntu = sct.grab(ekran)
                img = Image.frombytes("RGB", goruntu.size, goruntu.bgra, "raw", "BGRX")
                if SCALE != 1.0:
                    img = img.resize((int(img.width*SCALE), int(img.height*SCALE)), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=QUALITY)
                veri = buf.getvalue()
                # Bug #2 fix: timeout ile gönder — ağ donarsa thread sonsuza takılmaz
                conn.settimeout(YAYIN_SEND_TIMEOUT)
                conn.sendall(struct.pack('>I', len(veri)) + veri)
                conn.settimeout(None)
            except Exception:
                with baglantilar_lock:
                    bilgi = baglantilar.get(ip)
                    if bilgi:
                        bilgi['aktif'] = False
                        try:
                            bilgi['conn'].close()
                        except:
                            pass
                        del baglantilar[ip]
                break
            gecen = time.time() - t0
            bekle = aralik - gecen
            if bekle > 0:
                time.sleep(bekle)
    GLib.idle_add(pencere.pc_baglanti_kesildi, ip)

def _baglan_thread(ip, pencere, pencereli_mod=False):
    try:
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.settimeout(5)
        conn.connect((ip, 5558))
        conn.settimeout(None)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # Pencere modunu client'a gonder: 'W' = pencereli, 'F' = penceresiz (fullscreen)
        mod_byte = b'W' if pencereli_mod else b'F'
        conn.sendall(mod_byte)
        with baglantilar_lock:
            baglantilar[ip] = {'conn': conn, 'aktif': True}
        GLib.idle_add(pencere.pc_baglandi, ip)
        threading.Thread(target=yayin_dongusu, args=(ip, pencere), daemon=True).start()
    except Exception:
        GLib.idle_add(pencere.pc_hata, ip, None)

def baglantiyi_kes(ip, pencere):
    with baglantilar_lock:
        bilgi = baglantilar.get(ip)
        if bilgi:
            bilgi['aktif'] = False
            try:
                bilgi['conn'].sendall(struct.pack('>I', 0xFFFFFFFF))
                bilgi['conn'].close()
            except:
                pass
            del baglantilar[ip]
    GLib.idle_add(pencere.pc_baglanti_kesildi, ip)

# =============================================================================
#  ÖĞRENCİ EKRANI İZLEME + KONTROL
# =============================================================================

def _kare_al(conn):
    """Bağlantıdan 4 byte header + veri okur, JPEG bytes döner."""
    boyut_b = b''
    while len(boyut_b) < 4:
        p = conn.recv(4 - len(boyut_b))
        if not p:
            raise ConnectionError()
        boyut_b += p
    boyut = struct.unpack('>I', boyut_b)[0]
    if boyut == 0xFFFFFFFF:
        return None
    return _tam_al(conn, boyut)


def onizleme_dongusu(ip, pencere_ref):
    """Öğrenciden sürekli ONIZLEME_FPS kare çekip kartı günceller. PC kaldırılınca durur."""
    while True:
        # PC hâlâ listede mi? Değilse thread'i durdur
        with son_gorunme_lock:
            gorundu = son_gorunme.get(ip, 0)
        if time.time() - gorundu > 10:
            # Temizlik: memory leak önle
            with onizleme_kareleri_lock:
                onizleme_kareleri.pop(ip, None)
            break

        try:
            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.settimeout(5)
            conn.connect((ip, IZLE_PORT))
            conn.settimeout(3.0 / ONIZLEME_FPS + 2)
            mod = json.dumps({'mod': 'izleme'}).encode()
            conn.sendall(struct.pack('>I', len(mod)) + mod)
            while True:
                # PC kaldırıldı mı kontrol et
                with son_gorunme_lock:
                    if ip not in son_gorunme:
                        break
                kare = _kare_al(conn)
                if kare is None:
                    break
                with onizleme_kareleri_lock:
                    onizleme_kareleri[ip] = kare
                # Doğrudan worker thread'den çağır — içeride decode/resize
                # yapılır, GLib.idle_add sadece pixbuf atamasında kullanılır
                pencere_ref.onizleme_guncelle(ip, kare)
        except Exception:
            pass
        finally:
            try: conn.close()
            except: pass

        time.sleep(3)  # Yeniden bağlanmayı bekle


def buyuk_izleme_ac(ip, ad, pencere_ref):
    """
    Her IP için tek izleme penceresi açar. Zaten açıksa öne getirir.
    KRİTİK: Bu fonksiyon worker thread'den çağrılabilir, ama GTK widget'ları
    SADECE main thread'de oluşturulabilir. Bu yüzden tüm pencere
    oluşturma işi GLib.idle_add ile main thread'e devredilir.
    """
    with acik_izleme_pencereleri_lock:
        mevcut = acik_izleme_pencereleri.get(ip)
        if mevcut == "_olusturuluyor_":
            # Pencere şu anda main thread'de oluşturuluyor — tekrar deneme
            return
        if mevcut is not None:
            # Zaten açık — öne getir (sadece bu da main thread'de olmalı)
            GLib.idle_add(mevcut.present)
            return
        # Slot'u hemen "rezerve et" — race condition'ı engeller.
        # Gerçek pencere main thread'de oluşturulunca buraya yazılacak.
        acik_izleme_pencereleri[ip] = "_olusturuluyor_"

    # Pencere oluşturma işini main thread'e devret
    GLib.idle_add(_buyuk_izleme_olustur, ip, ad, pencere_ref)


def _buyuk_izleme_olustur(ip, ad, pencere_ref):
    """SADECE main thread'den çağrılır — GTK widget oluşturma burada güvenli."""
    try:
        pen = BuyukIzlemePencere(ip, ad, pencere_ref)
        with acik_izleme_pencereleri_lock:
            acik_izleme_pencereleri[ip] = pen
        pen.show_all()
    except Exception:
        # Pencere oluşturulamadı — rezerve edilen slot'u temizle ki
        # IP sonsuza kadar "_olusturuluyor_" durumunda takılı kalmasın
        with acik_izleme_pencereleri_lock:
            if acik_izleme_pencereleri.get(ip) == "_olusturuluyor_":
                acik_izleme_pencereleri.pop(ip, None)
    return False  # GLib.idle_add tek seferlik çalışsın


class BuyukIzlemePencere(Gtk.Window):
    """
    Öğrenci ekranını büyük pencerede gösterir.
    Fare ve klavye olayları xdotool ile öğrenciye iletilir.
    Kare alımı ve komut gönderimi AYRI soketler üzerinden yapılır.

    Performans tasarımı:
    - JPEG decode + resize işlemi WORKER THREAD'de yapılır (CPU-pahalı).
    - GTK main thread'e sadece hazır Pixbuf "idle_add" ile verilir (ucuz).
    - Eğer önceki kare henüz işlenip ekrana basılmadıysa, yeni gelen kare
      ATLANIR (frame-drop) — böylece kuyruk birikip GTK main loop tıkanmaz.
    """
    def __init__(self, ip, ad, ana_pencere):
        super().__init__(title=f"📺 {ad}  ({ip})")
        self.ip = ip
        self.ad = ad
        self.ana = ana_pencere
        self.aktif = True
        self.conn_izle    = None
        self.conn_kontrol = None
        self.set_default_size(1280, 760)
        self.set_size_request(700, 500)
        self.connect("destroy", self._kapat)

        try:
            from gi.repository import GdkPixbuf
            yol = _varlik_yolu('powerconnect-small.png')
            if yol:
                self.set_icon(GdkPixbuf.Pixbuf.new_from_file(yol))
        except Exception:
            pass

        ana_kutu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(ana_kutu)

        # Başlık
        baslik_kutu = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        baslik_kutu.set_margin_top(6); baslik_kutu.set_margin_bottom(6)
        baslik_kutu.set_margin_start(10); baslik_kutu.set_margin_end(10)
        ana_kutu.pack_start(baslik_kutu, False, False, 0)

        baslik_lbl = Gtk.Label()
        baslik_lbl.set_markup(f'<b>📺 {_guvenli_metin(ad)}</b>  <span color="#888" size="small">{_guvenli_metin(ip)}</span>')
        baslik_kutu.pack_start(baslik_lbl, True, True, 0)

        self.durum_lbl = Gtk.Label()
        self.durum_lbl.set_markup('<span color="#888" size="small">Bağlanıyor...</span>')
        baslik_kutu.pack_end(self.durum_lbl, False, False, 0)

        ana_kutu.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        # Ekran alanı — koyu arkaplan, siyah köşe hissi azaltılır
        self.event_box = Gtk.EventBox()
        self.event_box.set_can_focus(True)
        self.event_box.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.KEY_PRESS_MASK |
            Gdk.EventMask.KEY_RELEASE_MASK |
            Gdk.EventMask.SCROLL_MASK
        )
        self.event_box.connect("button-press-event",   self._fare_tus_bas)
        self.event_box.connect("button-release-event", self._fare_tus_birak)
        self.event_box.connect("motion-notify-event",  self._fare_hareket)
        self.event_box.connect("key-press-event",      self._klavye_bas)
        self.event_box.connect("scroll-event",         self._scroll)
        # Koyu gri arkaplan — siyah şerit yerine daha az göze batan ton
        self.event_box.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.12, 0.12, 0.12, 1))
        ana_kutu.pack_start(self.event_box, True, True, 0)

        # Image'ı ortalayan bir Box içine koy — gerçek ekran alanına tam oturması için
        img_kutu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        img_kutu.set_valign(Gtk.Align.CENTER)
        img_kutu.set_halign(Gtk.Align.CENTER)
        self.event_box.add(img_kutu)

        self.image = Gtk.Image()
        img_kutu.pack_start(self.image, True, True, 0)

        ana_kutu.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        alt = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        alt.set_margin_top(4); alt.set_margin_bottom(6)
        alt.set_margin_start(10); alt.set_margin_end(10)
        ana_kutu.pack_start(alt, False, False, 0)

        bilgi = Gtk.Label()
        bilgi.set_markup('<span color="#888" size="small">Ekrana tıkla/yaz → öğrenciye iletilir</span>')
        alt.pack_start(bilgi, True, True, 0)

        kapat_btn = Gtk.Button(label="✕  Kapat")
        kapat_btn.connect("clicked", lambda w: self.destroy())
        alt.pack_end(kapat_btn, False, False, 0)

        # Gerçek görüntü boyutları — koordinat dönüşümü için
        self._uzak_w = 1280
        self._uzak_h = 720
        self._img_w = 1280
        self._img_h = 720
        self._img_offset_x = 0
        self._img_offset_y = 0
        self._son_fare_t = 0
        self.kontrol_baglandi = False

        # Frame-drop mekanizması: aynı anda sadece 1 kare işleniyor olabilir
        self._isleniyor_lock = threading.Lock()
        self._isleniyor = False

        threading.Thread(target=self._kare_al_dongusu, daemon=True).start()
        threading.Thread(target=self._kontrol_baglanti_dongusu, daemon=True).start()

    def _kare_al_dongusu(self):
        """Kare alır, worker thread'de decode+resize yapar, sadece sonucu main thread'e yollar."""
        while self.aktif:
            conn = None
            try:
                conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                conn.settimeout(5)
                conn.connect((self.ip, IZLE_PORT))
                conn.settimeout(20)
                mod = json.dumps({'mod': 'izleme'}).encode()
                conn.sendall(struct.pack('>I', len(mod)) + mod)
                self.conn_izle = conn
                GLib.idle_add(self.durum_lbl.set_markup,
                              '<span color="#27ae60" size="small">● Bağlandı — kontrol aktif</span>')
                while self.aktif:
                    kare = _kare_al(conn)
                    if kare is None:
                        break
                    # Frame-drop: önceki kare hâlâ işleniyorsa bu kareyi atla
                    with self._isleniyor_lock:
                        if self._isleniyor:
                            continue
                        self._isleniyor = True
                    self._kare_isle_worker(kare)
            except Exception:
                if self.aktif:
                    GLib.idle_add(self.durum_lbl.set_markup,
                                  '<span color="#e74c3c" size="small">✗ Bağlanılamadı, yeniden deneniyor...</span>')
                    # Eski kareyi temizle ki donuk görüntü kalmasın
                    GLib.idle_add(self._image_temizle)
            finally:
                try: conn.close()
                except: pass
                self.conn_izle = None
            if self.aktif:
                time.sleep(2)

    def _image_temizle(self):
        """image.clear() güvenli sarmalayıcı — exception fırlatırsa idle_add callback zincirini bozmaz."""
        if not self.aktif:
            return False
        try:
            self.image.clear()
        except Exception:
            try:
                self.image.set_from_pixbuf(None)
            except Exception:
                pass
        return False

    def _kare_isle_worker(self, veri):
        """
        JPEG decode (pahalı, GTK'sız) worker thread'de yapılır.
        Boyut hesabı (event_box.get_allocation) GTK çağrısıdır —
        main thread'e devredilir, worker'da YAPILMAZ.
        """
        try:
            img = Image.open(io.BytesIO(veri)).convert('RGB')
            self._uzak_w, self._uzak_h = img.size
            GLib.idle_add(self._kare_isle_main_thread, img)
        except Exception:
            with self._isleniyor_lock:
                self._isleniyor = False

    def _kare_isle_main_thread(self, img):
        """SADECE main thread'den çağrılır — get_allocation burada güvenli."""
        if not self.aktif:
            # Pencere kapatılmış olabilir — destroyed widget'a dokunma
            with self._isleniyor_lock:
                self._isleniyor = False
            return False
        try:
            from gi.repository import GdkPixbuf
            alloc = self.event_box.get_allocation()
            alan_w = max(alloc.width, 1)
            alan_h = max(alloc.height, 1)

            img_oran = self._uzak_w / max(self._uzak_h, 1)
            alan_oran = alan_w / max(alan_h, 1)
            if img_oran > alan_oran:
                yeni_w = alan_w
                yeni_h = int(alan_w / img_oran)
            else:
                yeni_h = alan_h
                yeni_w = int(alan_h * img_oran)
            yeni_w = max(yeni_w, 1)
            yeni_h = max(yeni_h, 1)

            self._img_w = yeni_w
            self._img_h = yeni_h
            self._img_offset_x = (alan_w - yeni_w) // 2
            self._img_offset_y = (alan_h - yeni_h) // 2

            img = img.resize((yeni_w, yeni_h), Image.BILINEAR)
            raw = img.tobytes()
            pb = GdkPixbuf.Pixbuf.new_from_data(
                raw, GdkPixbuf.Colorspace.RGB, False, 8,
                yeni_w, yeni_h, yeni_w * 3)
            pb._raw = raw  # Referansı canlı tut — GC almasın
            self.image.set_from_pixbuf(pb)
        except Exception:
            pass
        with self._isleniyor_lock:
            self._isleniyor = False
        return False

    def _kontrol_baglanti_dongusu(self):
        """Sadece kontrol komutları gönderir — keepalive ile canlı tutar."""
        while self.aktif:
            conn = None
            try:
                conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                conn.settimeout(5)
                conn.connect((self.ip, IZLE_PORT))
                conn.settimeout(15)
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 5)
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3)
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                mod = json.dumps({'mod': 'kontrol'}).encode()
                conn.sendall(struct.pack('>I', len(mod)) + mod)
                self.conn_kontrol = conn
                self.kontrol_baglandi = True
                GLib.idle_add(self.durum_lbl.set_markup,
                              '<span color="#27ae60" size="small">● Bağlandı — kontrol aktif</span>')
                while self.aktif:
                    time.sleep(0.1)
                    # _komut_gonder gönderim hatasında conn_kontrol'ü None yapar —
                    # bunu görürsek hemen yeniden bağlan, getpeername() beklemeyelim
                    if self.conn_kontrol is None:
                        break
                    try:
                        conn.getpeername()
                    except Exception:
                        break
            except Exception:
                pass
            finally:
                self.kontrol_baglandi = False
                if self.aktif:
                    # Kontrol koptu ama izleme açık kalabilir — kullanıcıyı
                    # uyar, yoksa ekranı görmeye devam edip tıklamalarının
                    # neden işe yaramadığını anlayamaz.
                    GLib.idle_add(self.durum_lbl.set_markup,
                                  '<span color="#e67e22" size="small">⚠ Kontrol bağlantısı koptu, yeniden bağlanıyor...</span>')
                try: conn.close()
                except: pass
                self.conn_kontrol = None
            if self.aktif:
                time.sleep(2)

    def _koordinat_donustur(self, lx, ly):
        """
        event_box koordinatını uzak ekran koordinatına çevirir.
        image widget'ının event_box içindeki gerçek pozisyonunu
        translate_coordinates ile alır — manuel offset hesabına güvenmez.
        """
        try:
            # image widget'ının event_box içindeki gerçek sol-üst köşesi
            ok, ix_offset, iy_offset = self.image.translate_coordinates(
                self.event_box, 0, 0)
            if not ok:
                ix_offset, iy_offset = self._img_offset_x, self._img_offset_y
        except Exception:
            ix_offset, iy_offset = self._img_offset_x, self._img_offset_y

        ix = lx - ix_offset
        iy = ly - iy_offset
        ix = max(0, min(ix, self._img_w))
        iy = max(0, min(iy, self._img_h))
        x = int(ix / max(self._img_w, 1) * self._uzak_w)
        y = int(iy / max(self._img_h, 1) * self._uzak_h)
        return max(0, min(x, self._uzak_w)), max(0, min(y, self._uzak_h))

    def _komut_gonder(self, komut_dict):
        """Kontrol komutunu öğrenciye kontrol soketi üzerinden gönderir."""
        conn = self.conn_kontrol
        if conn is None:
            return
        try:
            veri = json.dumps(komut_dict).encode()
            conn.sendall(struct.pack('>I', len(veri)) + veri)
        except Exception:
            # Gönderim başarısız — bağlantı kopmuş demektir.
            # getpeername() bunu her zaman yakalayamayabilir, bu yüzden
            # burada da bağlantıyı düşürüp yeniden bağlanma döngüsünü tetikle.
            self.conn_kontrol = None
            try: conn.close()
            except: pass

    def _fare_hareket(self, widget, event):
        simdi = time.time()
        if simdi - self._son_fare_t < 0.03:  # max ~33/sn
            return
        self._son_fare_t = simdi
        x, y = self._koordinat_donustur(event.x, event.y)
        self._komut_gonder({'tip': 'fare_hareket', 'x': x, 'y': y})

    def _fare_tus_bas(self, widget, event):
        self.event_box.grab_focus()
        x, y = self._koordinat_donustur(event.x, event.y)
        self._komut_gonder({'tip': 'fare_bas', 'x': x, 'y': y, 'tus': event.button})

    def _fare_tus_birak(self, widget, event):
        x, y = self._koordinat_donustur(event.x, event.y)
        self._komut_gonder({'tip': 'fare_birak', 'x': x, 'y': y, 'tus': event.button})

    def _klavye_bas(self, widget, event):
        self._komut_gonder({'tip': 'klavye', 'keyval': event.keyval, 'keyname': Gdk.keyval_name(event.keyval) or ''})

    def _scroll(self, widget, event):
        yon = 'asagi' if event.direction == Gdk.ScrollDirection.DOWN else 'yukari'
        self._komut_gonder({'tip': 'scroll', 'yon': yon})

    def _kapat(self, *a):
        self.aktif = False
        with acik_izleme_pencereleri_lock:
            acik_izleme_pencereleri.pop(self.ip, None)
        for conn in [self.conn_izle, self.conn_kontrol]:
            if conn:
                try: conn.close()
                except: pass

# =============================================================================
#  DOSYA GONDERME (host → ogrenci masaustu)
# =============================================================================

DOSYA_CHUNK = 256 * 1024  # 256 KB chunk — RAM'e tüm dosyayı yüklemez

def _tek_dosya_gonder(ip, tam_yol, goreli_yol):
    """Tek dosyayı chunk'lı olarak gönderir. RAM'e tamamını yüklemez."""
    conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    conn.settimeout(10)
    conn.connect((ip, DOSYA_AL_PORT))
    conn.settimeout(60)
    ad_bytes = goreli_yol.encode()
    dosya_boyutu = os.path.getsize(tam_yol)
    # Önce: ad uzunluğu + ad + dosya boyutu
    conn.sendall(struct.pack('>I', len(ad_bytes)) + ad_bytes +
                 struct.pack('>I', dosya_boyutu))
    # Sonra: dosyayı chunk'lı gönder
    with open(tam_yol, 'rb') as f:
        while True:
            chunk = f.read(DOSYA_CHUNK)
            if not chunk:
                break
            conn.sendall(chunk)
    conn.close()

def _klasor_gonder_recursive(ip, yerel_yol, goreli_yol):
    """Klasoru recursive olarak gonderir, yol yapisi korunur."""
    for isim in sorted(os.listdir(yerel_yol)):
        tam_yol = os.path.join(yerel_yol, isim)
        goreli = os.path.join(goreli_yol, isim)
        if os.path.isfile(tam_yol):
            _tek_dosya_gonder(ip, tam_yol, goreli)
        elif os.path.isdir(tam_yol):
            _klasor_gonder_recursive(ip, tam_yol, goreli)

def dosya_gonder(ip, dosya_yolu, pencere):
    try:
        dosya_adi = os.path.basename(dosya_yolu)
        if os.path.isdir(dosya_yolu):
            klasor_adi = os.path.basename(dosya_yolu.rstrip('/'))
            _klasor_gonder_recursive(ip, dosya_yolu, klasor_adi)
        else:
            _tek_dosya_gonder(ip, dosya_yolu, dosya_adi)
        GLib.idle_add(pencere.durum_goster, f"✓ {dosya_adi} → {ip} gonderildi")
    except Exception:
        GLib.idle_add(pencere.durum_goster, f"✗ Hata ({ip}): Dosya gönderilemedi, bağlantı kurulamadı")

# =============================================================================
#  DOSYA GEZGINI
# =============================================================================

def gezgin_komut_gonder(ip, komut_dict):
    conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    conn.settimeout(5)
    conn.connect((ip, GEZGIN_PORT))
    conn.settimeout(10)
    komut_bytes = json.dumps(komut_dict).encode()
    conn.sendall(struct.pack('>I', len(komut_bytes)) + komut_bytes)
    yanit_len = struct.unpack('>I', _tam_al(conn, 4))[0]
    yanit = json.loads(_tam_al(conn, yanit_len).decode())
    return conn, yanit

def _masaustu_bul():
    """Bug #3 fix: Büyük/küçük harf ve farklı isim varyantlarını dener."""
    for aday in ['~/Masaüstü', '~/masaüstü', '~/Desktop', '~/desktop']:
        yol = os.path.expanduser(aday)
        if os.path.isdir(yol):
            return yol
    return os.path.expanduser('~')

def gezgin_indir(ip, uzak_yol, dizin_mi, pencere, durum_label_ref):
    """
    Dosya veya klasoru oldugu gibi masaustune indirir.
    dizin_mi=True ise klasor yapisi recursive olarak cekilir.
    """
    masaustu = _masaustu_bul()  # Bug #3 fix

    try:
        if not dizin_mi:
            # Tek dosya indir
            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.settimeout(5)
            conn.connect((ip, GEZGIN_PORT))
            conn.settimeout(60)
            komut = json.dumps({'komut': 'indir', 'yol': uzak_yol}).encode()
            conn.sendall(struct.pack('>I', len(komut)) + komut)
            meta_len = struct.unpack('>I', _tam_al(conn, 4))[0]
            meta = json.loads(_tam_al(conn, meta_len).decode())
            if meta.get('durum') == 'ok':
                # Bug #1 fix: chunk'lı indir — RAM'e tamamını yüklemez
                dosya_len = struct.unpack('>I', _tam_al(conn, 4))[0]
                hedef = os.path.join(masaustu, meta['isim'])
                kalan = dosya_len
                with open(hedef, 'wb') as f:
                    while kalan > 0:
                        chunk = conn.recv(min(256 * 1024, kalan))
                        if not chunk:
                            raise ConnectionError("Bağlantı kesildi")
                        f.write(chunk)
                        kalan -= len(chunk)
                GLib.idle_add(durum_label_ref.set_text, f"İndirildi: {meta['isim']}")
            else:
                GLib.idle_add(durum_label_ref.set_text, f"Hata: {meta.get('mesaj')}")
            conn.close()
        else:
            # Klasor indir - recursive
            klasor_adi = os.path.basename(uzak_yol.rstrip('/'))
            hedef_kok = os.path.join(masaustu, klasor_adi)
            _klasor_indir_recursive(ip, uzak_yol, hedef_kok, durum_label_ref)
            GLib.idle_add(durum_label_ref.set_text, f"İndirildi: {klasor_adi}")
    except Exception:
        GLib.idle_add(durum_label_ref.set_text, "Hata: İndirme başarısız oldu, bağlantı kurulamadı")

def _klasor_indir_recursive(ip, uzak_yol, yerel_yol, durum_label_ref=None):
    """Bug #5 fix: Tek kalıcı bağlantıyla tüm klasörü indirir — her dosya için reconnect yok."""
    os.makedirs(yerel_yol, exist_ok=True)
    try:
        # Tek bağlantı aç, tüm işlemleri bu bağlantıdan yap
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.settimeout(5)
        conn.connect((ip, GEZGIN_PORT))
        conn.settimeout(60)
    except:
        return

    def _listele(yol):
        try:
            komut = json.dumps({'komut': 'listele', 'yol': yol}).encode()
            conn.sendall(struct.pack('>I', len(komut)) + komut)
            yanit_len = struct.unpack('>I', _tam_al(conn, 4))[0]
            return json.loads(_tam_al(conn, yanit_len).decode())
        except:
            return {'durum': 'hata'}

    def _indir_dosya(uzak, yerel):
        try:
            komut = json.dumps({'komut': 'indir', 'yol': uzak}).encode()
            conn.sendall(struct.pack('>I', len(komut)) + komut)
            meta_len = struct.unpack('>I', _tam_al(conn, 4))[0]
            meta = json.loads(_tam_al(conn, meta_len).decode())
            if meta.get('durum') == 'ok':
                dosya_len = struct.unpack('>I', _tam_al(conn, 4))[0]
                kalan = dosya_len
                with open(yerel, 'wb') as f:
                    while kalan > 0:
                        chunk = conn.recv(min(256 * 1024, kalan))
                        if not chunk:
                            raise ConnectionError()
                        f.write(chunk)
                        kalan -= len(chunk)
        except:
            pass

    def _recursive(uzak, yerel):
        os.makedirs(yerel, exist_ok=True)
        yanit = _listele(uzak)
        if yanit.get('durum') != 'ok':
            return
        for g in yanit.get('girişler', []):
            yerel_hedef = os.path.join(yerel, g['isim'])
            if durum_label_ref:
                GLib.idle_add(durum_label_ref.set_text, f"İndiriliyor: {g['isim']}")
            if g['dizin']:
                _recursive(g['yol'], yerel_hedef)
            else:
                _indir_dosya(g['yol'], yerel_hedef)

    try:
        _recursive(uzak_yol, yerel_yol)
    finally:
        try:
            conn.sendall(struct.pack('>I', len(b'{"komut":"kapat"}')) + b'{"komut":"kapat"}')
            conn.close()
        except:
            pass

class GezginPencere(Gtk.Window):
    def __init__(self, ip, ad, ana_pencere):
        super().__init__(title=f"Dosya Gezgini — {ad} ({ip})")
        self.ip = ip
        self.ad = ad
        self.ana = ana_pencere
        self.set_default_size(700, 500)
        self.mevcut_yol = '/home'
        try:
            from gi.repository import GdkPixbuf
            yol = _varlik_yolu('powerconnect-small.png')
            if yol:
                pb = GdkPixbuf.Pixbuf.new_from_file(yol)
                self.set_icon(pb)
        except Exception:
            pass

        ana = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(ana)

        # Yol cubugu
        yol_kutu = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        yol_kutu.set_margin_top(8); yol_kutu.set_margin_bottom(8)
        yol_kutu.set_margin_start(10); yol_kutu.set_margin_end(10)
        ana.pack_start(yol_kutu, False, False, 0)

        geri_btn = Gtk.Button(label="←")
        geri_btn.connect("clicked", self.geri_git)
        yol_kutu.pack_start(geri_btn, False, False, 0)

        ev_btn = Gtk.Button(label="🏠")
        ev_btn.connect("clicked", lambda w: self.listele('/home'))
        yol_kutu.pack_start(ev_btn, False, False, 0)

        self.yol_label = Gtk.Label(label="/home")
        self.yol_label.set_xalign(0)
        yol_kutu.pack_start(self.yol_label, True, True, 0)

        ana.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        # Dosya listesi
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        ana.pack_start(scrolled, True, True, 0)

        self.store = Gtk.ListStore(str, str, bool, str, str)  # ikon, isim, dizin, tam_yol, boyut_str
        self.treeview = Gtk.TreeView(model=self.store)
        self.treeview.connect("row-activated", self.satir_tikla)

        col_ikon = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
        col_ikon.set_min_width(30)
        self.treeview.append_column(col_ikon)

        col_isim = Gtk.TreeViewColumn("Ad", Gtk.CellRendererText(), text=1)
        col_isim.set_expand(True)
        self.treeview.append_column(col_isim)

        col_boyut = Gtk.TreeViewColumn("Boyut", Gtk.CellRendererText(), text=4)
        self.treeview.append_column(col_boyut)

        scrolled.add(self.treeview)

        # Alt buton
        alt = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        alt.set_margin_top(8); alt.set_margin_bottom(8)
        alt.set_margin_start(10); alt.set_margin_end(10)
        ana.pack_start(alt, False, False, 0)

        self.durum = Gtk.Label(label="Yukleniyor...")
        alt.pack_start(self.durum, True, True, 0)

        indir_btn = Gtk.Button(label="⬇  Secili Dosyayi Indir")
        indir_btn.get_style_context().add_class("suggested-action")
        indir_btn.connect("clicked", self.indir)
        alt.pack_end(indir_btn, False, False, 0)

        self.show_all()
        self.listele('/home')

    def listele(self, yol):
        self.durum.set_text("Yukleniyor...")
        def _yap():
            try:
                conn, yanit = gezgin_komut_gonder(self.ip, {'komut': 'listele', 'yol': yol})
                conn.close()
                if yanit.get('durum') == 'ok':
                    GLib.idle_add(self._listeyi_goster, yanit['girişler'], yol)
                else:
                    GLib.idle_add(self.durum.set_text, f"Hata: {yanit.get('mesaj')}")
            except Exception:
                GLib.idle_add(self.durum.set_text, "Hata: Bağlantı kurulamadı")
        threading.Thread(target=_yap, daemon=True).start()

    def _listeyi_goster(self, girişler, yol):
        self.store.clear()
        self.mevcut_yol = yol
        self.yol_label.set_text(yol)
        for g in girişler:
            ikon = "📁" if g['dizin'] else "📄"
            if g['dizin']:
                boyut = ""
            else:
                b = g['boyut']
                if b < 1024:
                    boyut = f"{b} B"
                elif b < 1024*1024:
                    boyut = f"{b//1024} KB"
                else:
                    boyut = f"{b//1024//1024} MB"
            self.store.append([ikon, g['isim'], g['dizin'], g['yol'], boyut])
        self.durum.set_text(f"{len(girişler)} öge")

    def satir_tikla(self, treeview, path, column):
        it = self.store.get_iter(path)
        dizin_mi = self.store.get_value(it, 2)
        tam_yol  = self.store.get_value(it, 3)
        if dizin_mi:
            self.listele(tam_yol)

    def geri_git(self, widget):
        ust = os.path.dirname(self.mevcut_yol)
        if ust and ust != self.mevcut_yol:
            self.listele(ust)

    def indir(self, widget):
        secim = self.treeview.get_selection()
        model, it = secim.get_selected()
        if it:
            tam_yol = model.get_value(it, 3)
            dizin_mi = model.get_value(it, 2)
            self.durum.set_text("İndiriliyor...")
            threading.Thread(
                target=gezgin_indir,
                args=(self.ip, tam_yol, dizin_mi, self.ana, self.durum),
                daemon=True
            ).start()

# =============================================================================
#  PC KARTI
# =============================================================================

class PCKarti(Gtk.Frame):
    """
    Kart düzeni:
    ┌─────────────────────┐
    │  [canlı önizleme]   │  ← tıklayınca büyük izleme penceresi açılır
    │     IP adresi       │
    │     Bekliyor        │
    │  [✓]  [▶ Bağlan]   │
    └─────────────────────┘
    """
    ONIZLEME_W = 200
    ONIZLEME_H = 120

    def __init__(self, ad, ip, pencere):
        super().__init__()
        self.ad = ad
        self.ip = ip
        self.pencere_ref = pencere
        self.bagli = False
        self.secili = False
        self.cevrimdisi = False
        self._onizleme_aktif = False

        self.set_margin_top(6); self.set_margin_bottom(6)
        self.set_margin_start(6); self.set_margin_end(6)

        kutu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        kutu.set_margin_top(8); kutu.set_margin_bottom(8)
        kutu.set_margin_start(8); kutu.set_margin_end(8)
        self.add(kutu)

        # ── Ekran önizleme alanı ──
        oniz_event = Gtk.EventBox()
        oniz_event.connect("button-press-event", self._onizleme_tikla)
        oniz_event.set_tooltip_text("Tıkla → büyük pencerede aç + kontrol et")
        kutu.pack_start(oniz_event, False, False, 0)

        self.onizleme_img = Gtk.Image()
        self.onizleme_img.set_size_request(self.ONIZLEME_W, self.ONIZLEME_H)
        self._varsayilan_onizleme()
        oniz_event.add(self.onizleme_img)

        # ── IP ──
        self.ip_label = Gtk.Label()
        self.ip_label.set_markup(f'<span color="#888" size="small">{ip}</span>')
        kutu.pack_start(self.ip_label, False, False, 0)

        # ── Durum ──
        self.durum_label = Gtk.Label()
        self.durum_label.set_markup('<span color="#888" size="small">Bekliyor</span>')
        kutu.pack_start(self.durum_label, False, False, 0)

        # ── Alt satır: tik + buton ──
        alt = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        kutu.pack_start(alt, False, False, 0)

        self.check = Gtk.CheckButton()
        self.check.connect("toggled", self.secim_degisti)
        alt.pack_start(self.check, False, False, 0)

        self.btn = Gtk.Button(label="▶  Bağlan")
        self.btn.get_style_context().add_class("suggested-action")
        self.btn.connect("clicked", self.btn_tikla)
        self.btn.set_hexpand(True)
        alt.pack_start(self.btn, True, True, 0)

        # Sağ tık
        self.connect("button-press-event", self.sag_tik)

        self.show_all()

        # Önizleme thread'ini başlat
        self._onizleme_baslat()

    def _varsayilan_onizleme(self):
        """Bağlantı yokken gri ekran ikonu gösterir."""
        try:
            from gi.repository import GdkPixbuf
            pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8,
                                       self.ONIZLEME_W, self.ONIZLEME_H)
            pb.fill(0x2a2a2aff)
            self.onizleme_img.set_from_pixbuf(pb)
        except Exception:
            self.onizleme_img.set_from_icon_name("video-display", Gtk.IconSize.DIALOG)

    def _onizleme_baslat(self):
        if self._onizleme_aktif:
            return
        self._onizleme_aktif = True
        threading.Thread(target=onizleme_dongusu,
                         args=(self.ip, self.pencere_ref),
                         daemon=True).start()

    def onizleme_guncelle(self, veri):
        """Worker thread'den çağrılır — decode/resize burda, sadece pixbuf ataması main thread'e gider."""
        try:
            from gi.repository import GdkPixbuf
            img = Image.open(io.BytesIO(veri)).convert('RGB')
            img = img.resize((self.ONIZLEME_W, self.ONIZLEME_H), Image.BILINEAR)
            raw = img.tobytes()
            pb = GdkPixbuf.Pixbuf.new_from_data(
                raw, GdkPixbuf.Colorspace.RGB, False, 8,
                self.ONIZLEME_W, self.ONIZLEME_H, self.ONIZLEME_W * 3)
            pb._raw = raw
            GLib.idle_add(self._pixbuf_ata, pb)
        except Exception:
            pass

    def _pixbuf_ata(self, pb):
        try:
            self.onizleme_img.set_from_pixbuf(pb)
        except Exception:
            pass
        return False

    def _onizleme_tikla(self, widget, event):
        """Önizlemeye tıklanınca büyük izleme + kontrol penceresi açar.
        Bu zaten GTK signal handler'ı (main thread) — direkt çağrılır."""
        if event.button == 1:
            buyuk_izleme_ac(self.ip, self.ad, self.pencere_ref)

    def sag_tik(self, widget, event):
        if event.button == 3:
            menu = Gtk.Menu()
            item_izle = Gtk.MenuItem(label="📺  Büyük Ekranda İzle + Kontrol Et")
            item_izle.connect("activate", lambda w: buyuk_izleme_ac(self.ip, self.ad, self.pencere_ref))
            menu.append(item_izle)
            menu.append(Gtk.SeparatorMenuItem())
            item_gezgin = Gtk.MenuItem(label="📂  Dosya Gezgini")
            item_gezgin.connect("activate", lambda w: self.gezgini_ac())
            menu.append(item_gezgin)
            menu.show_all()
            menu.popup_at_pointer(event)
            return True

    def gezgini_ac(self):
        pencere = GezginPencere(self.ip, self.ad, self.pencere_ref)
        pencere.show()

    def secim_degisti(self, widget):
        self.secili = widget.get_active()
        self.pencere_ref.secim_guncelle()

    def btn_tikla(self, widget):
        if not self.bagli:
            self.btn.set_sensitive(False)
            self.durum_label.set_markup('<span color="#f39c12" size="small">Baglaniliyor...</span>')
            pencereli = (self.pencere_ref.global_mod_combo.get_active_id() == "pencereli")
            threading.Thread(target=_baglan_thread, args=(self.ip, self.pencere_ref, pencereli), daemon=True).start()
        else:
            self.btn.set_sensitive(False)
            threading.Thread(target=baglantiyi_kes, args=(self.ip, self.pencere_ref), daemon=True).start()

    def set_bagli(self):
        self.bagli = True
        self.cevrimdisi = False
        self.durum_label.set_markup('<span color="#27ae60" size="small">● Yayın aktif</span>')
        self.btn.set_label("■  Geri Sal")
        self.btn.get_style_context().remove_class("suggested-action")
        self.btn.get_style_context().add_class("destructive-action")
        self.btn.set_sensitive(True)

    def set_kesildi(self):
        self.bagli = False
        self.cevrimdisi = False
        self.durum_label.set_markup('<span color="#888" size="small">Bekliyor</span>')
        self.btn.set_label("▶  Baglan")
        self.btn.get_style_context().remove_class("destructive-action")
        self.btn.get_style_context().add_class("suggested-action")
        self.btn.set_sensitive(True)

    def set_cevrimdisi(self):
        self.cevrimdisi = True
        self.durum_label.set_markup('<span color="#e67e22" size="small">⚠ Cevrimdisi</span>')
        self.btn.set_label("▶  Baglan")
        self.btn.get_style_context().remove_class("destructive-action")
        self.btn.get_style_context().add_class("suggested-action")
        self.btn.set_sensitive(True)

    def set_hata(self, mesaj):
        self.bagli = False
        self.durum_label.set_markup('<span color="#e74c3c" size="small">✗ Bağlantı kurulamadı</span>')
        self.btn.set_label("▶  Tekrar Dene")
        self.btn.set_sensitive(True)

    def eslesiyor(self, arama):
        if not arama:
            return True
        return arama.lower() in self.ad.lower() or arama.lower() in self.ip.lower()

# =============================================================================
#  ANA PENCERE
# =============================================================================

class HostPencere(Gtk.Window):

    def __init__(self):
        super().__init__(title="PowerConnect — Yönetici Paneli")
        self.set_default_size(1000, 650)
        self.connect("destroy", self.kapat)

        # Gorev cubugu ve pencere logosu
        self.set_icon_name("powerconnect")
        try:
            from gi.repository import GdkPixbuf
            yol = _varlik_yolu('powerconnect.png')
            if yol:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(yol, 28, 28, True)
                self.set_icon(pb)
        except Exception:
            try:
                self.set_icon_name("network-wired")
            except Exception:
                pass

        self.pc_listesi = {}
        self.kartlar    = {}

        ana = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(ana)

        ust = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ust.set_margin_top(10); ust.set_margin_bottom(8)
        ust.set_margin_start(12); ust.set_margin_end(12)
        ana.pack_start(ust, False, False, 0)

        # Sol ust logo
        try:
            from gi.repository import GdkPixbuf
            yol = _varlik_yolu('powerconnect.png')
            if yol:
                logo_pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(yol, 28, 28, True)
                logo_img = Gtk.Image.new_from_pixbuf(logo_pb)
                ust.pack_start(logo_img, False, False, 0)
        except Exception:
            pass

        baslik = Gtk.Label()
        baslik.set_markup('<b>PowerConnect — Yönetici Paneli</b>')
        ust.pack_start(baslik, False, False, 0)

        self.arama = Gtk.SearchEntry()
        self.arama.set_placeholder_text("PC ara...")
        self.arama.set_size_request(250, -1)
        self.arama.connect("search-changed", self.arama_degisti)
        ust.pack_start(self.arama, False, False, 0)

        self.btn_hepsi = Gtk.Button(label="⚡  Hepsine Baglan")
        self.btn_hepsi.get_style_context().add_class("suggested-action")
        self.btn_hepsi.connect("clicked", self.hepsine_baglan)
        ust.pack_start(self.btn_hepsi, False, False, 0)

        self.btn_geri = Gtk.Button(label="■  Hepsini Geri Sal")
        self.btn_geri.get_style_context().add_class("destructive-action")
        self.btn_geri.connect("clicked", self.hepsini_geri_sal)
        ust.pack_start(self.btn_geri, False, False, 0)

        self.sayac_label = Gtk.Label()
        self.sayac_label.set_markup('<span color="#888">0 PC</span>')
        ust.pack_end(self.sayac_label, False, False, 0)

        # Global baglanti turu dropdown - sag uste (sayacin solunda)
        self.global_mod_combo = Gtk.ComboBoxText()
        self.global_mod_combo.append("penceresiz", "🖥  Penceresiz")
        self.global_mod_combo.append("pencereli",  "⧉  Pencereli")
        self.global_mod_combo.set_active_id("penceresiz")
        self.global_mod_combo.set_tooltip_text("Tüm bağlantılar için ekran modu")
        ust.pack_end(self.global_mod_combo, False, False, 0)

        ana.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        ana.pack_start(scrolled, True, True, 0)

        self.flow = Gtk.FlowBox()
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_max_children_per_line(10)
        self.flow.set_min_children_per_line(1)
        self.flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow.set_margin_top(10)
        self.flow.set_margin_start(6); self.flow.set_margin_end(6)
        scrolled.add(self.flow)

        ana.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        alt = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        alt.set_margin_top(8); alt.set_margin_bottom(10)
        alt.set_margin_start(12); alt.set_margin_end(12)
        ana.pack_start(alt, False, False, 0)

        self.btn_sec = Gtk.Button(label="☑  Hepsini Sec")
        self.btn_sec.connect("clicked", self.hepsini_sec)
        alt.pack_start(self.btn_sec, False, False, 0)

        self.btn_sec_kaldir = Gtk.Button(label="☐  Secimi Kaldir")
        self.btn_sec_kaldir.connect("clicked", self.secimi_kaldir)
        alt.pack_start(self.btn_sec_kaldir, False, False, 0)

        self.secili_label = Gtk.Label()
        self.secili_label.set_markup('<span color="#888">0 secili</span>')
        alt.pack_start(self.secili_label, False, False, 0)

        self.durum_bar = Gtk.Label()
        self.durum_bar.set_markup('<span color="#888" size="small"></span>')
        alt.pack_start(self.durum_bar, True, True, 0)

        self.btn_dosya = Gtk.Button(label="📁  Dosya At")
        self.btn_dosya.connect("clicked", self.dosya_sec)
        alt.pack_end(self.btn_dosya, False, False, 0)

    def onizleme_guncelle(self, ip, veri):
        """Öğrenci önizleme karesini ilgili PCKarti'na iletir."""
        if ip in self.kartlar:
            self.kartlar[ip].onizleme_guncelle(veri)

    def pc_baglandi(self, ip):
        if ip in self.kartlar:
            self.kartlar[ip].set_bagli()
        # En az bir baglanti varsa global combo'yu kilitle
        with baglantilar_lock:
            bagli_sayisi = len(baglantilar)
        self.global_mod_combo.set_sensitive(bagli_sayisi == 0)

    def pc_baglanti_kesildi(self, ip):
        if ip in self.kartlar:
            self.kartlar[ip].set_kesildi()
        # Hic baglanti kalmadiysa global combo'yu serbest birak
        with baglantilar_lock:
            bagli_sayisi = len(baglantilar)
        self.global_mod_combo.set_sensitive(bagli_sayisi == 0)

    def pc_kaldir(self, ip):
        if ip in self.kartlar:
            kart = self.kartlar[ip]
            child = kart.get_parent()
            if child:
                self.flow.remove(child)
            del self.kartlar[ip]
            self.pc_listesi.pop(ip, None)
            self._sayac_guncelle()

    def pc_cevrimdisi(self, ip):
        """PC'yi listeden silme, sadece cevrimdisi olarak isaretler."""
        if ip in self.kartlar:
            self.kartlar[ip].set_cevrimdisi()

    def pc_guncelle(self, ad, ip):
        with son_gorunme_lock:
            son_gorunme[ip] = time.time()
        if ip in self.kartlar:
            # Zaten var - cevrimdisi isaretliyse tekrar aktif yap
            kart = self.kartlar[ip]
            if kart.cevrimdisi and not kart.bagli:
                kart.durum_label.set_markup('<span color="#888" size="small">Bekliyor</span>')
                kart.cevrimdisi = False
            return
        self.pc_listesi[ip] = ad
        kart = PCKarti(ad, ip, self)
        self.kartlar[ip] = kart
        self.flow.add(kart)
        self.flow.show_all()
        self._filtrele()
        self._sayac_guncelle()

    def pc_hata(self, ip, mesaj):
        if ip in self.kartlar:
            self.kartlar[ip].set_hata(mesaj)

    def _sayac_guncelle(self):
        n = len(self.kartlar)
        self.sayac_label.set_markup(f'<span color="#888">{n} PC</span>')

    def arama_degisti(self, widget):
        self._filtrele()

    def _filtrele(self):
        arama = self.arama.get_text().strip()
        for ip, kart in self.kartlar.items():
            child = kart.get_parent()
            if child:
                child.set_visible(kart.eslesiyor(arama))

    def hepsine_baglan(self, widget):
        pencereli = (self.global_mod_combo.get_active_id() == "pencereli")
        # Anlık kopya al — döngü sırasında bir PC çevrimdışı olup
        # self.kartlar değişirse "dictionary changed size during
        # iteration" hatasını önler.
        for ip, kart in list(self.kartlar.items()):
            child = kart.get_parent()
            if child and child.get_visible() and not kart.bagli:
                kart.btn.set_sensitive(False)
                kart.durum_label.set_markup('<span color="#f39c12" size="small">Baglaniliyor...</span>')
                threading.Thread(target=_baglan_thread, args=(ip, self, pencereli), daemon=True).start()

    def hepsini_geri_sal(self, widget):
        for ip in list(baglantilar.keys()):
            threading.Thread(target=baglantiyi_kes, args=(ip, self), daemon=True).start()

    def hepsini_sec(self, widget):
        for kart in self.kartlar.values():
            child = kart.get_parent()
            if child and child.get_visible():
                kart.check.set_active(True)

    def secimi_kaldir(self, widget):
        for kart in self.kartlar.values():
            kart.check.set_active(False)

    def secim_guncelle(self):
        n = sum(1 for k in self.kartlar.values() if k.secili)
        self.secili_label.set_markup(f'<span color="#888">{n} secili</span>')

    def dosya_sec(self, widget):
        dialog = Gtk.FileChooserDialog(
            title="Gonderilecek Dosya veya Klasoru Sec", parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            "Gonder", Gtk.ResponseType.OK
        )
        try:
            from gi.repository import GdkPixbuf
            yol = _varlik_yolu('powerconnect-small.png')
            if yol:
                pb = GdkPixbuf.Pixbuf.new_from_file(yol)
                dialog.set_icon(pb)
        except Exception:
            pass

        yanit = dialog.run()
        if yanit == Gtk.ResponseType.OK:
            dosya_yolu = dialog.get_filename()
            if dosya_yolu:
                secili_ipler = [ip for ip, k in self.kartlar.items() if k.secili]
                if not secili_ipler:
                    secili_ipler = list(self.kartlar.keys())
                if secili_ipler:
                    for ip in secili_ipler:
                        threading.Thread(target=dosya_gonder, args=(ip, dosya_yolu, self), daemon=True).start()
                    isim = os.path.basename(dosya_yolu)
                    self.durum_goster(f"{isim} → {len(secili_ipler)} PC ye gonderiliyor...")
                else:
                    self.durum_goster("Hic PC yok!")
        dialog.destroy()

    def durum_goster(self, mesaj):
        self.durum_bar.set_markup(f'<span color="#888" size="small">{mesaj}</span>')

    def kapat(self, *args):
        with baglantilar_lock:
            for ip, bilgi in baglantilar.items():
                try:
                    bilgi['conn'].sendall(struct.pack('>I', 0xFFFFFFFF))
                    bilgi['conn'].close()
                except:
                    pass
        Gtk.main_quit()

def main():
    ag_baglantisini_hazirla()  # Arka planda ag baglantisini hazirla
    pencere = HostPencere()
    pencere.show_all()
    threading.Thread(target=broadcast_dinle, args=(pencere,), daemon=True).start()
    threading.Thread(target=kopuk_kontrol, args=(pencere,), daemon=True).start()
    Gtk.main()

if __name__ == '__main__':
    main()
