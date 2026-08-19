# Cài đặt trên máy Roblox

Chỉ cần **1 file duy nhất**: `roblox-auto-rejoin.exe` - không cần cài Python,
không cần venv, `handle.exe` đã được nhúng sẵn bên trong.

## Bước 1 - Copy file

Copy `roblox-auto-rejoin.exe` vào 1 thư mục riêng trên máy Roblox, ví dụ:
`C:\Tools\roblox-auto-rejoin\roblox-auto-rejoin.exe`

(Đừng để trong `Downloads`/Desktop trực tiếp - `config.json`/`cookies.txt`
sẽ được tạo NGAY CẠNH file .exe này, nên cần 1 thư mục cố định.)

## Bước 2 - Accept EULA của handle.exe (1 lần duy nhất)

`handle.exe` (Sysinternals) cần được accept EULA riêng cho **user Windows
đang đăng nhập trên máy này** - key lưu trong registry của Windows, làm ở
máy backend rồi không tự áp dụng sang máy này được. Mở PowerShell:

```powershell
# Chạy app 1 lần trước để nó tự giải nén handle.exe ra thư mục temp,
# rồi tìm thư mục đó (in ra trong config.json sau khi chạy lần đầu, key
# "handle_exe_path", dạng C:\Users\<user>\AppData\Local\Temp\_MEIxxxxx\handle.exe)
# rồi chạy:
& "<đường dẫn handle_exe_path đọc từ config.json>" -accepteula
```

Hoặc đơn giản hơn: mở app lên, để nó chạy watch 1 lần (sẽ tự thử gọi
handle.exe, không sao nếu lần đầu lỗi), sau đó chạy PowerShell trên với
đường dẫn đọc được trong `config.json`.

## Bước 3 - Điền cookies.txt

Chạy `roblox-auto-rejoin.exe` 1 lần để nó tự tạo `config.json`/
`cookies.txt` cạnh file .exe, đóng app lại, mở `cookies.txt` bằng Notepad,
dán mỗi account 1 dòng (xem README.md gốc để biết cú pháp per-account
place id / private server).

## Bước 4 - Chỉnh Settings

Mở app, vào Settings, kiểm tra/sửa:

- **Potassium.exe path** - đường dẫn Potassium THẬT trên máy Roblox này
  (khác máy dev, gần như chắc chắn phải đổi).
- **Place ID** mặc định nếu cần.
- Các mục khác (window layout, webhook, FPS...) tùy chọn.

## Bước 5 - Chạy thật

Bấm "Reload accounts" rồi "Start watching".

## Bước 6 - Control API (nếu muốn dùng dashboard web từ xa)

App tự mở 1 API server nội bộ ở port **8765** ngay khi khởi động (mặc định
`0.0.0.0` - nghe trên cả LAN, không chỉ máy này). API key tự sinh, xem
trong Settings -> Control API, hoặc trong `config.json` (key `api_key`).

- Nếu dashboard web (website-cloner) chạy **trên máy khác**, cần mở
  firewall port 8765 trên máy Roblox này (PowerShell chạy với quyền
  Admin):
  ```powershell
  New-NetFirewallRule -DisplayName "Rejoin API 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
  ```
- Cập nhật `.env.local` của website-cloner: `REJOIN_API_URL=http://<IP máy Roblox này>:8765`
  và `REJOIN_API_KEY=<api_key đọc từ config.json/Settings>`.
- Không cần bước này nếu chỉ dùng app này độc lập, không kèm dashboard web.

---

Cần cập nhật code mới sau này: build lại `.exe` ở máy dev
(`py -m PyInstaller --name roblox-auto-rejoin --onefile --windowed
--add-data "handle.exe;." main.py`), copy đè file `.exe` mới sang - KHÔNG
đè `config.json`/`cookies.txt` đã điền trên máy Roblox.
