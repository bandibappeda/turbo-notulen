# splitmedia

Aplikasi terminal interaktif untuk memisahkan audio dan video, baik dari **URL** (YouTube, dan situs lain yang didukung [yt-dlp](https://github.com/yt-dlp/yt-dlp)) maupun dari **file lokal**.

```
╭──────────────────────────────────────────────╮
│                  splitmedia                  │
│   Download · pisah audio/video · potong segmen│
╰──────────────────────────────────────────────╯
```

## Fitur

| Menu | Kemampuan |
|------|-----------|
| **Unduh dari URL** | audio saja (MP3/M4A/Opus/WAV/FLAC, pilih bitrate) · video saja tanpa suara (MP4) · audio + video **terpisah** jadi dua file |
| **File lokal** | ekstrak audio · ekstrak video (tanpa suara) · pisahkan keduanya sekaligus |
| **Split audio** | pecah audio jadi banyak bagian: per **titik potong** (`00:29:59`) atau **durasi tetap** (tiap `00:29:59`) |
| **Potong segmen** | ambil satu rentang waktu tertentu (`30-90`, `00:30-01:30`, `1:02:03-1:05:00`) dari video, audio, atau gabungan |
| **Gabung teks** | gabungkan 2 atau lebih file `.txt`/`.md`/teks lain jadi satu file, sesuai urutan pilihan Anda |

### Split audio

Audio (dari file audio maupun track audio sebuah video) bisa dipecah tanpa encode ulang:

- **Titik potong** — ketik satu atau beberapa waktu, dipisah spasi/koma:
  `00:29:59` · `00:10:00, 01:02:03` · `10:00 20:00`
- **Durasi tetap** — ketik panjang tiap bagian, mis. `00:29:59`;
  file 1 jam menjadi `00:00:00–00:29:59`, `00:29:59–00:59:58`, `00:59:58–01:00:00`.

Format waktu yang diterima: `HH:MM:SS`, `MM:SS`, atau jumlah detik (`90`). Hasil diberi nama
`judul - bagian 01 (00-00-00 to 00-29-59).mp3`. Preview rencana split ditampilkan sebelum
diproses, dan codec asli dipertahankan (MP3 tetap MP3, AAC tetap M4A, dst.).

> Potongan dibuat dengan *stream copy* sehingga sangat cepat dan tanpa penurunan kualitas.
> Akibat pembulatan ke batas frame codec, durasi tiap bagian bisa berselisih beberapa
> milidetik (AAC/MP3) — untuk audio PCM (WAV/FLAC) dipakai encoder lossless agar tepat.


Detail lain:

- Progress bar unduh + konversi (kecepatan, sisa waktu, ukuran).
- Nama file otomatis rapi; file yang sudah ada **tidak pernah ditimpa** — diberi suffix `(2)`, `(3)`, dst.
- Beberapa URL sekaligus dalam satu antrean.
- Video non-MP4 (VP9/AV1) dipindah ke MKV tanpa re-encode; audio bisa dikonversi ke format apa pun.
- Semua pesan dan menu berbahasa Indonesia.

### Gabung file teks

Menu utama → **5. Gabung file teks**. Tambahkan file satu per baris, lalu atur urutannya
sebelum digabung.

**Memasukkan file** (tiap baris satu input):

| Input | Arti |
|---|---|
| `D:\buku\bab 1.txt` | satu file (spasi boleh, tidak perlu tanda kutip) |
| `D:\buku\bab 1.txt, D:\buku\bab 2.txt` | beberapa file, dipisah **koma** |
| `D:\buku\*.txt` | semua `.txt` di folder itu |
| `D:\buku` | seluruh isi folder |
| `folder` | diminta path folder, lalu semua file diambil |

> Spasi **bukan** pemisah antar file, karena banyak nama file mengandung spasi. Gunakan koma.

**Urutan** — bisa dibiarkan sesuai input, atau diurutkan otomatis menurut nama
(`bab 2` sebelum `bab 10`, bukan urutan abjad biasa), menurut tanggal diubah, dibalik,
atau ditentukan sendiri dengan mengetik nomor (`3 1 2`).

**Gaya penggabungan**: sambung langsung · satu baris kosong antar file · dengan judul nama file.

**Encoding** — setiap file dideteksi otomatis (UTF-8, UTF-8 BOM, UTF-16, Windows-1252, Latin-1),
jadi campuran file lama dan baru tetap aman. File biner (mis. gambar yang salah berekstensi
`.txt`) dilewati dengan peringatan. Hasil disimpan sebagai UTF-8, UTF-8+BOM, atau ANSI
sesuai pilihan, dengan akhir baris sesuai OS (CRLF di Windows).

## Memasang di komputer lain

Cukup **salin folder proyek** (tanpa `.venv`) ke komputer tujuan, lalu klik dua kali **`run.bat`**.

Saat pertama dibuka, `run.bat` memeriksa semua persyaratan dan menampilkan checklist:

```
  ╭──────────────────────────────────────────────────────────╮
  │                       splitmedia                         │
  │         Download · pisah audio/video · gabung teks       │
  │         Penyiapan pertama · otomatis & sekali saja       │
  ╰──────────────────────────────────────────────────────────╯

  Memeriksa persyaratan
  ✔ Python                                      3.12.10
  ✘ Lingkungan aplikasi                         belum dibuat
  ✘ Paket Python                                kurang: yt-dlp, rich, imageio-ffmpeg
  ✘ FFmpeg                                      disalin otomatis (tanpa unduh)

  ! 3 bagian perlu disiapkan.                   izin diminta sebelum mengunduh
```

Sebelum mengunduh apa pun, installer **menampilkan rencana dan perkiraan ukuran**, lalu meminta
konfirmasi. Setelah itu semua disiapkan otomatis dengan progress bar, diverifikasi ulang, dan
aplikasi langsung terbuka. Kunjungan berikutnya tinggal membuka `run.bat` — pengecekan hanya
sekali dan aplikasi langsung jalan.

Yang disiapkan otomatis:

| Persyaratan | Perilaku |
|---|---|
| **Python 3.9+** | Dicari di `py` launcher, PATH, dan folder instalasi. Bila tidak ada, installer Python resmi diunduh dari python.org dan dipasang untuk pengguna ini (tanpa administrator). |
| **Lingkungan aplikasi** | `.venv` dibuat dengan Python yang ditemukan (diutamakan 3.10–3.12). |
| **Paket Python** | `yt-dlp`, `rich`, `imageio-ffmpeg` dari PyPI (~79 MB). |
| **FFmpeg** | Diambil dari paket `imageio-ffmpeg` yang baru dipasang — **tanpa unduhan 120 MB terpisah**. Disalin ke `splitmedia\.setup\bin`. |

Butuh koneksi internet hanya pada penyiapan pertama. Bila terputus, jalankan ulang `run.bat` —
bagian yang sudah selesai tidak diunduh lagi.

### Di komputer ini punya Python?

Bisa juga manual (tanpa `run.bat`):

```bash
python -m venv .venv
.venv\Scriptsctivate
pip install -e .
python -m splitmedia
```

Aplikasi tetap mencari FFmpeg sendiri lewat `SPLITMEDIA_FFMPEG`, lalu `PATH`, lalu paket
`imageio-ffmpeg`, lalu `splitmedia\.setup\bin`.

### Pindah drive / pindah komputer lagi

Folder `.setup` menyimpan beberapa hal yang terikat ke lokasi & komputer (path ffmpeg, tanda
siap). Menyalin seluruh folder ke komputer lain tetap aman: jalankan `run.bat` dan hanya bagian
yang rambu-rambunya tidak cocok yang disiapkan ulang (biasanya hanya penyalinan FFmpeg, ±1 detik).
Untuk instalasi dari nol, hapus folder `.venv` dan `splitmedia\.setup` sebelum disalin.

### Log

Bila ada yang gagal, installer menyimpan log di `splitmedia\.setup\logs\` dan menampilkan
lokasinya beserta langkah yang bisa dicoba.

## Catatan

- Unduh hanya konten yang boleh Anda simpan; hargai hak cipta dan Ketentuan Layanan situs sumber.
- `yt-dlp` sering diperbarui untuk mengikuti perubahan situs. Bila unduhan gagal karena ekstraktor usang:

  ```bash
  pip install -U yt-dlp
  ```

- Folder hasil default adalah folder tempat Anda menjalankan aplikasi; menu akan menanyakan folder output setiap kali.
- Format `WAV`/`FLAC` tidak memakai pengaturan bitrate karena bersifat lossless.
- Split audio mempertahankan codec asli; bila file sumber tidak punya track audio, menu akan menolak dengan pesan jelas.

## Struktur proyek

```
run.bat                  # titik masuk: cek persyaratan lalu buka aplikasi
installer/
  bootstrap.py       # pemeriksaan, rencana, pemasangan, verifikasi
  uikit.py           # tampilan/progress installer (hanya stdlib)
  fetch-python.ps1   # cadangan bila Python belum ada sama sekali
splitmedia/
  __main__.py        # entry point `python -m splitmedia`
  app.py             # alur menu utama
  ui.py              # tampilan, prompt, progress bar
  downloader.py      # integrasi yt-dlp (probe, unduh, potong)
  operations.py      # operasi file lokal (ekstrak, pisah, potong)
  textmerge.py       # gabung file teks (deteksi encoding, urutan)
  ffmpeg_tools.py    # pencarian ffmpeg, probe media, perintah ffmpeg
```
