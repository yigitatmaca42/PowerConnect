# PowerConnect

**Pardus tabanlı açık kaynaklı sınıf yönetim aracı**

PowerConnect, öğretmenin ekranını öğrenci bilgisayarlarına gerçek zamanlı olarak yayınlamasını, dosya göndermesini ve öğrenci dosya sistemine erişmesini sağlayan hafif ve kurulumu kolay bir uygulamadır. Tamamen Pardus Linux üzerinde geliştirilmiş olup yerli ve milli ekosisteme katkı sağlamayı hedeflemektedir.

---

## Özellikler

- **Otomatik Keşif** — Aynı ağdaki öğrenci PC'ler UDP broadcast ile otomatik bulunur, IP girişi gerekmez
- **Gerçek Zamanlı Ekran Yayını** — Öğretmen ekranı 30 FPS ile öğrencilere iletilir
- **Öğrenci Kilidi** — Yayın sırasında öğrenci klavye/fare kullanamaz, pencereyi kapatamaz
- **Çoklu Bağlantı** — Aynı anda birden fazla öğrenci PC'ye bağlanılabilir
- **Toplu İşlem** — Hepsine Bağlan / Hepsini Geri Sal
- **PC Arama** — PC adı veya IP'ye göre anlık filtreleme
- **Dosya Gönderme** — Seçili PC'lere dosya gönderilebilir, otomatik masaüstüne kaydedilir
- **Uzak Dosya Gezgini** — Öğrenci dosya sistemi görüntülenebilir, dosya ve klasörler indirilebilir
- **Silinmez Kurulum** — `chattr +i` ile kilitli, format dışında silinemez
- **Otomatik Başlatma** — Systemd user servisi olarak her açılışta çalışır

---

## Dosyalar

| Dosya | Açıklama |
|---|---|
| `bin/PowerConnect` | Öğretmen uygulaması (ELF) — çift tıkla aç |
| `bin/user` | Öğrenci kurulum uygulaması (ELF) — bir kez `sudo ./user` ile çalıştır |
| `src/host.py` | Öğretmen uygulaması kaynak kodu |
| `src/user.py` | Öğrenci uygulaması kaynak kodu |

---

## Kurulum

### Öğrenci PC'ler

`user` dosyasını öğrenci PC'ye kopyala ve **bir kez** çalıştır:

```bash
sudo ./user
```

"Kurulum Tamamlandı. Pencereyi kapatabilirsiniz." mesajı görününce biter. Bilgisayar her açıldığında `user` otomatik başlar, bir daha dokunmana gerek yok.

> `user` dosyası `/opt/powerconnect/user` konumuna kopyalanır ve kilitlenir. `rm -rf` ile bile silinemez, yalnızca format atılarak kaldırılabilir.

### Öğretmen PC

Kurulum gerekmez. `PowerConnect` dosyasını çift tıklayarak aç.

---

## Kullanım

### Bağlantı

Uygulama açıldığında aynı ağdaki tüm öğrenci PC'ler otomatik olarak panelde listelenir. İstediğiniz PC'lere tek tek veya toplu olarak bağlanabilirsiniz.

![Toplu ve tekil bağlantı](screenshots/resim_1.png)

Birden fazla PC'ye bağlı kalırken istediğiniz PC'leri bağlı tutup diğerlerini serbest bırakabilirsiniz.

![Seçili bağlantı yönetimi](screenshots/resim_2.png)

### PC Arama ve Filtreleme

Çok sayıda PC olduğunda arama çubuğuna yazdığınız ifadeyle listeyi daraltabilirsiniz. Örneğin "lab6" yazınca yalnızca adında "lab6" geçen PC'ler görünür. "Hepsine Bağlan" butonu yalnızca filtredeki PC'lere bağlanır, diğerlerine dokunmaz.

![PC arama ve filtreleme](screenshots/resim_3.png)

### Uzak Dosya Gezgini

Herhangi bir PC kartına sağ tıklayarak o bilgisayarın dosyalarına göz atabilirsiniz.

![Sağ tık menüsü](screenshots/resim_4.png)

Gezgin otomatik olarak öğrencinin home dizinini açar. Sol ok tuşuna basarak üst dizine çıkabilir, "/" kök dizininden istediğiniz klasöre ulaşabilirsiniz.

![Kök dizinine gitme](screenshots/resim_5.png)

Öğrenci PC'nin tüm klasör yapısını kendi bilgisayarınızdaki gibi gezebilirsiniz.

![Klasör gezintisi](screenshots/resim_6.png)

Masaüstüne geldiğinizde masaüstündeki tüm dosya ve klasörler listelenir.

![Masaüstü içeriği](screenshots/resim_7.png)

### Dosya İndirme

İstediğiniz dosyayı (klasör, zip, txt ve diğer tüm türler) seçip "Seçili Dosyayı İndir" butonuna basarak kendi bilgisayarınıza çekebilirsiniz.

![Dosya seçimi](screenshots/resim_8.png)

İndirme tamamlandığında hangi dosyanın masaüstüne indirildiği altta gösterilir.

![İndirme tamamlandı](screenshots/resim_9.png)

### Dosya Gönderme

Dosya göndermek istediğiniz PC'leri seçmek için ya her PC kartının sol üst köşesindeki kutucuğa tıklayabilir ya da alttaki "Hepsini Seç" butonunu kullanabilirsiniz. Seçimi kaldırmak için yanındaki "Seçimi Kaldır" butonu kullanılır.

![PC seçimi](screenshots/resim_10.png)

PC'leri seçtikten sonra "Dosya At" butonuna basınca dosya seçici açılır. Sol panelden istediğiniz konuma giderek göndermek istediğiniz dosyayı seçebilirsiniz.

> **Not:** Klasör göndermek için önce klasörü zip/rar olarak sıkıştırmanız gerekmektedir. Sıkıştırılmamış klasör gönderimi hata verir.

![Dosya seçici](screenshots/resim_11.png)

"Gönder" butonuna basıldığında uygulama önceki ekrana döner ve altta kaç PC'ye gönderildiği bilgisi gösterilir.

![Gönderim başladı](screenshots/resim_12.png)

Dosya sırayla tüm seçili PC'lere gönderilir, en son hangi PC'ye ulaştığı anlık olarak güncellenir.

![Gönderim durumu](screenshots/resim_13.png)

Gönderilen dosya, öğrenci PC'sinin masaüstüne sorunsuz şekilde düşer.

![Masaüstüne ulaştı](screenshots/resim_14.png)

---

## Teknik Bilgiler

| | |
|---|---|
| Dil | Python 3 |
| Arayüz | GTK3 (PyGObject) |
| Derleme | PyInstaller (ELF) |
| Keşif | UDP Broadcast (port 5559) |
| Ekran Yayını | TCP (port 5558) |
| Dosya Gönderme | TCP (port 5557) |
| Dosya Gezgini | TCP (port 5556) |
| Lisans | GPL-3.0 |

---

## Kaynak Koddan Derleme

```bash
pip3 install pyinstaller mss Pillow --break-system-packages

# Öğretmen uygulaması
pyinstaller --onefile src/host.py -n PowerConnect

# Öğrenci uygulaması
pyinstaller --onefile src/user.py -n user
```

---

## Lisans

GNU General Public License v3.0 — bkz. [LICENSE](LICENSE)
