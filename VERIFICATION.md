# Báo cáo rà soát – Smart Scale BMI 2026.11.1 (sửa SQLite lock)

Ngày: 2026-11-07. Mục tiêu: Home Assistant Core 2026.11 / Marketplace (HACS tích hợp).

## Phạm vi thay đổi

- **Khởi động và khóa SQLite:** tạo đường dẫn HTTP và đăng ký service đúng một lần; `ConfigEntry.async_create_background_task` và executor không chặn HA startup. Shared per-HA coordinator chỉ chạy schema migration một lần cho toàn bộ profile; shared threaded write lock bảo vệ khi executor tiếp tục chạy sau khi task bị cancel. WAL là persistent: kiểm tra chế độ trước khi gọi `PRAGMA journal_mode=WAL`; serialize mọi SQLite write, nhưng không chặn read. Retry 3 lần khi lỗi SQLite, ghi warning rõ cho lock tạm thời và traceback khi không phục hồi.
- **Tương thích 2026.11:** dùng Probatio trực tiếp cho schemas; khởi tạo `DeviceInfo` dataclass; quản lý Lovelace resources theo loại `type` mới trong storage collection; giữ `hacs.json`, manifest Integration và kiểm tra hassfest.
- **Dữ liệu:** SQLite WAL, upsert profile không xóa lịch sử, chỉ ghi event khi weight/profile cùng phiên; chuyển đơn vị kg/g/lb/oz; chống trùng dựa trên timestamp nguồn; tính tuổi BMI theo ngày địa phương khi cân; truy vấn lịch sử theo thời gian thực (tránh sai thứ tự bản ghi ISO có múi giờ khác nhau).
- **Services:** `add_measurement`, `update_measurement`, `delete_measurement`; schema kiểm tra đầu vào, lỗi rõ, action UI có Config Entry selector và Datetime selector.
- **Thẻ Dashboard:** Shadow DOM, visual editor, Sections `getGridOptions`, `getStubConfig`, chỉnh theme và nhiều entity, quản lý lịch sử, hành động thêm/sửa/xóa, cache discovery 5 giây, dấu hiệu lỗi đã escape HTML.
- **Bảo toàn dữ liệu:** đường dẫn DB giữ nguyên; không tự xóa profile/historical measurements khi gỡ entry; dữ liệu cũ được đọc sau migration, nhưng vẫn khuyến nghị sao lưu thư mục `<config>/smart_scale_bmi/` trước nâng cấp.
- **CI:** GitHub Actions chạy Python 3.14 unit tests, JavaScript syntax/API smoke tests, HACS/Marketplace validation, hassfest.

## Các phép thử đã chạy trong môi trường kiểm tra (không phải Home Assistant thật)

- Python `compileall` toàn bộ mã Python: **PASS**.
- `python -m pytest -q`: **16 passed**. Bao gồm nâng cấp schema cũ, giữ lịch sử, thêm/sửa/xóa, chống trùng, sắp xếp múi giờ, tính BMI, ghi nhận trạng thái, SQLite WAL persistent, WAL chỉ kích hoạt một lần, kiểm tra `BEGIN EXCLUSIVE` từ kết nối ngoài, 8 thread cùng migrate, 12 hồ sơ cùng khởi tạo/migration chỉ một lần, retry sau lỗi `database is locked`, và 120 bản ghi đồng thời với kiểm tra `PRAGMA integrity_check`.
- `node --check custom_components/smart_scale_bmi/frontend/smart-scale-bmi-card.js`: **PASS**.
- `node tests/test_card.cjs`: **PASS**. Kiểm tra đăng ký custom element, Lovelace card methods, visual editor, HTML escaping, cache entity discovery.
- `unzip -t` bản phát hành: chạy khi đóng gói.

## Chưa thể xác nhận chỉ bằng kiểm thử tĩnh / mock

Chưa khởi chạy thử tích hợp trong một Home Assistant Core 2026.11.0 thật có browser, recorder và cân thông minh. Do đó **không thể cam kết tuyệt đối không có lỗi runtime hoặc lỗi UI do theme/custom cards khác**. GitHub Actions `hacs/action` và `hassfest` mới được **thêm workflow**, không có nghĩa đã chạy và đạt trên GitHub của người dùng.

## Checklist kiểm thử nghiệm thu trên Home Assistant

1. Sao lưu `<config>/smart_scale_bmi/` và tạo HA backup.
2. Cài repo dạng **Integration** trong Settings → Marketplace; đối chiếu metadata và Github Release tag `2026.11.1` với manifest.
3. Mở Settings → Devices & services → Smart Scale BMI → Add entry; cấu hình hai sensor cùng cân, kiểm tra `Options` và reload.
4. Restart Home Assistant, theo dõi thời gian startup và log `custom_components.smart_scale_bmi` mức debug; kiểm tra config entry loaded trước khi SQLite hoàn tất mà không chặn setup.
5. Thử cân lần lượt hai người; kiểm tra ID profile và weight đồng bộ trong 120 giây, không trùng, không ghép dữ liệu sai người; kiểm tra kg/lb và trạng thái unknown/unavailable.
6. Test add/update/delete qua Actions chọn Config Entry, rồi qua thẻ Dashboard; kiểm tra lịch sử hiển thị đúng ở chế độ Sections + Masonry, desktop + mobile, dark + light.
7. Kiểm tra `Settings → Dashboards → Resources`: chỉ một JS module `/smart_scale_bmi_ui/smart-scale-bmi-card.js`; trong môi trường YAML resource quản trị thủ công theo README.
8. Xuất Diagnostics; xác nhận không lộ tên/cân nặng/ngày sinh và số lịch sử khớp.
9. Chạy `hacs/action` và `hassfest` trên GitHub (workflow sẽ chạy sau push) và kiểm tra kết quả trước khi tạo Release chính thức.

## Phân tích nguyên nhân và biện pháp sửa (2026.11.1)

Log gốc trỏ tới `database.py` tại `PRAGMA journal_mode=WAL`. Bản trước gọi `init_db()` cho **mỗi config entry** trên cùng file SQLite, đồng thời mở nhiều connection ở các executor thread. `PRAGMA journal_mode=WAL` đòi hỏi lock khi chuyển chế độ journal, dẫn tới `sqlite3.OperationalError: database is locked` lúc startup, ngay cả khi `sqlite3.connect(timeout=15)`.

Đã sửa tại `custom_components/smart_scale_bmi/__init__.py` và `database.py`: coordinator dùng chung; WAL chỉ đổi chế độ khi cần; lock đa luồng cho schema/ghi; retry từ nền khi external lock; warning không spam traceback tại các lần retry và thông báo lỗi cuối cùng có hướng dẫn. Không đổi đường dẫn/schema data và không xóa lịch sử. Bản `2026.11.1` tăng release version và JavaScript card version để cập nhật resource cache Marketplace. Chưa kiểm tra trực tiếp trên hệ thống Home Assistant của người dùng.
