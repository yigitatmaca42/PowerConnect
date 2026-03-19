#!/usr/bin/env python3
# =============================================================================
#  host.py — Yonetici Paneli
# =============================================================================

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk

import socket, threading, struct, io, json, time, os
import mss
from PIL import Image

BROADCAST_PORT = 5559
FPS            = 30
QUALITY        = 50
SCALE          = 1.0
DOSYA_AL_PORT  = 5557
GEZGIN_PORT    = 5556

baglantilar      = {}
baglantilar_lock = threading.Lock()

def kendi_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

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
            if ad and ip and ip != kendi_ip():
                GLib.idle_add(pencere.pc_guncelle, ad, ip)
        except:
            pass

def _tam_al(conn, n):
    veri = b''
    while len(veri) < n:
        p = conn.recv(min(65536, n - len(veri)))
        if not p:
            raise ConnectionError()
        veri += p
    return veri

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
                conn.sendall(struct.pack('>I', len(veri)) + veri)
            except:
                break
            gecen = time.time() - t0
            bekle = aralik - gecen
            if bekle > 0:
                time.sleep(bekle)
    GLib.idle_add(pencere.pc_baglanti_kesildi, ip)

def _baglan_thread(ip, pencere):
    try:
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.settimeout(5)
        conn.connect((ip, 5558))
        conn.settimeout(None)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with baglantilar_lock:
            baglantilar[ip] = {'conn': conn, 'aktif': True}
        GLib.idle_add(pencere.pc_baglandi, ip)
        threading.Thread(target=yayin_dongusu, args=(ip, pencere), daemon=True).start()
    except Exception as e:
        GLib.idle_add(pencere.pc_hata, ip, str(e))

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
#  DOSYA GONDERME (host → ogrenci masaustu)
# =============================================================================

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
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.settimeout(10)
        conn.connect((ip, DOSYA_AL_PORT))
        conn.settimeout(None)
        dosya_adi = os.path.basename(dosya_yolu)
        with open(dosya_yolu, 'rb') as f:
            veri = f.read()
        ad_bytes = dosya_adi.encode()
        conn.sendall(struct.pack('>I', len(ad_bytes)) + ad_bytes +
                     struct.pack('>I', len(veri)) + veri)
        conn.close()
        GLib.idle_add(pencere.durum_goster, f"✓ {dosya_adi} → {ip} gonderildi")
    except Exception as e:
        GLib.idle_add(pencere.durum_goster, f"✗ Hata ({ip}): {str(e).replace(chr(91)+chr(69)+chr(114)+chr(114)+chr(110)+chr(111), chr(72)+chr(97)+chr(116)+chr(97))}")

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

def gezgin_indir(ip, uzak_yol, dizin_mi, pencere, durum_label_ref):
    """
    Dosya veya klasoru oldugu gibi masaustune indirir.
    dizin_mi=True ise klasor yapisi recursive olarak cekilir.
    """
    masaustu = os.path.expanduser('~/Masaüstü')
    if not os.path.exists(masaustu):
        masaustu = os.path.expanduser('~/Desktop')

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
                dosya_len = struct.unpack('>I', _tam_al(conn, 4))[0]
                dosya_veri = _tam_al(conn, dosya_len)
                hedef = os.path.join(masaustu, meta['isim'])
                with open(hedef, 'wb') as f:
                    f.write(dosya_veri)
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
    except Exception as e:
        GLib.idle_add(durum_label_ref.set_text, f"Hata: {e}")

def _klasor_indir_recursive(ip, uzak_yol, yerel_yol, durum_label_ref=None):
    os.makedirs(yerel_yol, exist_ok=True)
    try:
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.settimeout(5)
        conn.connect((ip, GEZGIN_PORT))
        conn.settimeout(10)
        komut = json.dumps({'komut': 'listele', 'yol': uzak_yol}).encode()
        conn.sendall(struct.pack('>I', len(komut)) + komut)
        yanit_len = struct.unpack('>I', _tam_al(conn, 4))[0]
        yanit = json.loads(_tam_al(conn, yanit_len).decode())
        conn.close()
    except:
        return

    if yanit.get('durum') != 'ok':
        return

    for g in yanit.get('girişler', []):
        yerel_hedef = os.path.join(yerel_yol, g['isim'])
        if g['dizin']:
            _klasor_indir_recursive(ip, g['yol'], yerel_hedef, durum_label_ref)
        else:
            try:
                conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                conn.settimeout(5)
                conn.connect((ip, GEZGIN_PORT))
                conn.settimeout(60)
                komut = json.dumps({'komut': 'indir', 'yol': g['yol']}).encode()
                conn.sendall(struct.pack('>I', len(komut)) + komut)
                meta_len = struct.unpack('>I', _tam_al(conn, 4))[0]
                meta = json.loads(_tam_al(conn, meta_len).decode())
                if meta.get('durum') == 'ok':
                    dosya_len = struct.unpack('>I', _tam_al(conn, 4))[0]
                    dosya_veri = _tam_al(conn, dosya_len)
                    with open(yerel_hedef, 'wb') as f:
                        f.write(dosya_veri)
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

        self.store = Gtk.ListStore(str, str, bool, str)  # ikon, isim, dizin, tam_yol
        self.treeview = Gtk.TreeView(model=self.store)
        self.treeview.connect("row-activated", self.satir_tikla)

        col_ikon = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
        col_ikon.set_min_width(30)
        self.treeview.append_column(col_ikon)

        col_isim = Gtk.TreeViewColumn("Ad", Gtk.CellRendererText(), text=1)
        col_isim.set_expand(True)
        self.treeview.append_column(col_isim)

        col_boyut = Gtk.TreeViewColumn("Boyut", Gtk.CellRendererText(), text=3)
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
            except Exception as e:
                GLib.idle_add(self.durum.set_text, f"Baglanti hatasi: {e}")
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
            self.store.append([ikon, g['isim'], g['dizin'], g['yol'], ])
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
    def __init__(self, ad, ip, pencere):
        super().__init__()
        self.ad = ad
        self.ip = ip
        self.pencere_ref = pencere
        self.bagli = False
        self.secili = False

        self.set_margin_top(6); self.set_margin_bottom(6)
        self.set_margin_start(6); self.set_margin_end(6)

        kutu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        kutu.set_margin_top(10); kutu.set_margin_bottom(10)
        kutu.set_margin_start(10); kutu.set_margin_end(10)
        self.add(kutu)

        self.check = Gtk.CheckButton()
        self.check.connect("toggled", self.secim_degisti)
        kutu.pack_start(self.check, False, False, 0)

        ikon = Gtk.Label()
        ikon.set_markup('<span size="xx-large">🖥</span>')
        kutu.pack_start(ikon, False, False, 0)

        self.ad_label = Gtk.Label()
        self.ad_label.set_markup(f'<b>{ad}</b>')
        kutu.pack_start(self.ad_label, False, False, 0)

        self.ip_label = Gtk.Label()
        self.ip_label.set_markup(f'<span color="#888" size="small">{ip}</span>')
        kutu.pack_start(self.ip_label, False, False, 0)

        self.durum_label = Gtk.Label()
        self.durum_label.set_markup('<span color="#888" size="small">Bekliyor</span>')
        kutu.pack_start(self.durum_label, False, False, 0)

        self.btn = Gtk.Button(label="▶  Baglan")
        self.btn.get_style_context().add_class("suggested-action")
        self.btn.connect("clicked", self.btn_tikla)
        kutu.pack_start(self.btn, False, False, 0)

        # Sag tik menu
        self.connect("button-press-event", self.sag_tik)

        self.show_all()

    def sag_tik(self, widget, event):
        if event.button == 3:
            menu = Gtk.Menu()
            item_gezgin = Gtk.MenuItem(label="📂  Dosyalara Goz At")
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
            threading.Thread(target=_baglan_thread, args=(self.ip, self.pencere_ref), daemon=True).start()
        else:
            self.btn.set_sensitive(False)
            threading.Thread(target=baglantiyi_kes, args=(self.ip, self.pencere_ref), daemon=True).start()

    def set_bagli(self):
        self.bagli = True
        self.durum_label.set_markup('<span color="#27ae60" size="small">● Yayin aktif</span>')
        self.btn.set_label("■  Geri Sal")
        self.btn.get_style_context().remove_class("suggested-action")
        self.btn.get_style_context().add_class("destructive-action")
        self.btn.set_sensitive(True)

    def set_kesildi(self):
        self.bagli = False
        self.durum_label.set_markup('<span color="#888" size="small">Bekliyor</span>')
        self.btn.set_label("▶  Baglan")
        self.btn.get_style_context().remove_class("destructive-action")
        self.btn.get_style_context().add_class("suggested-action")
        self.btn.set_sensitive(True)

    def set_hata(self, mesaj):
        self.bagli = False
        temiz = str(mesaj).replace('[Error', '[Hata').replace('Error', 'Hata')
        self.durum_label.set_markup(f'<span color="#e74c3c" size="small">✗ {temiz}</span>')
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
        super().__init__(title="PowerConnect — Yonetici Paneli")
        self.set_default_size(1000, 650)
        self.connect("destroy", self.kapat)

        self.pc_listesi = {}
        self.kartlar    = {}

        ana = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(ana)

        ust = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ust.set_margin_top(10); ust.set_margin_bottom(8)
        ust.set_margin_start(12); ust.set_margin_end(12)
        ana.pack_start(ust, False, False, 0)

        baslik = Gtk.Label()
        baslik.set_markup('<b>PowerConnect — Yonetici Paneli</b>')
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

    def pc_guncelle(self, ad, ip):
        if ip in self.kartlar:
            return
        self.pc_listesi[ip] = ad
        kart = PCKarti(ad, ip, self)
        self.kartlar[ip] = kart
        self.flow.add(kart)
        self.flow.show_all()
        self._filtrele()
        self._sayac_guncelle()

    def pc_baglandi(self, ip):
        if ip in self.kartlar:
            self.kartlar[ip].set_bagli()

    def pc_baglanti_kesildi(self, ip):
        if ip in self.kartlar:
            self.kartlar[ip].set_kesildi()

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
        for ip, kart in self.kartlar.items():
            child = kart.get_parent()
            if child and child.get_visible() and not kart.bagli:
                kart.btn.set_sensitive(False)
                kart.durum_label.set_markup('<span color="#f39c12" size="small">Baglaniliyor...</span>')
                threading.Thread(target=_baglan_thread, args=(ip, self), daemon=True).start()

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
        # Hem dosya hem klasor secimi
        dialog.set_action(Gtk.FileChooserAction.OPEN)
        dialog.set_select_multiple(False)
        yanit = dialog.run()
        if yanit == Gtk.ResponseType.OK:
            dosya_yolu = dialog.get_filename()
            secili_ipler = [ip for ip, k in self.kartlar.items() if k.secili]
            if not secili_ipler:
                secili_ipler = list(self.kartlar.keys())
            if secili_ipler:
                for ip in secili_ipler:
                    threading.Thread(target=dosya_gonder, args=(ip, dosya_yolu, self), daemon=True).start()
                self.durum_goster(f"{len(secili_ipler)} PC ye gonderiliyor...")
            else:
                self.durum_goster("Hic PC yok!")
        dialog.destroy()

    def durum_goster(self, mesaj):
        temiz = str(mesaj).replace('[Error', '[Hata').replace('Error', 'Hata')
        self.durum_bar.set_markup(f'<span color="#888" size="small">{temiz}</span>')

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
    pencere = HostPencere()
    pencere.show_all()
    threading.Thread(target=broadcast_dinle, args=(pencere,), daemon=True).start()
    Gtk.main()

if __name__ == '__main__':
    main()
