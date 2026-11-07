# Báo cáo rà soát – Smart Scale BMI 2026.11.0

Ngày: 2026-11-07. Mục tiêu: Home Assistant Core 2026.11 / Marketplace (HACS tích hợp).

## Phạm vi thay đổi

- **Khởi động:** tạo đường dẫn HTTP và đăng ký service đúng một lần; khởi tạo/migration SQLite chạy bằng `ConfigEntry.async_create_background_task` và executor, không chặn thời điểm đánh dấu config entry là đã setup. Tự retry ba lần khi storage lỗi và báo loại lỗi trong Diagnostics.
- **Tương thích 2026.11:** dùng Probatio trực tiếp cho schemas; khởi tạo `DeviceInfo` dataclass; quản lý Lovelace resources theo loại `type` mới trong storage collection; giữ `hacs.json`, manifest Integration và kiểm tra hassfest.
- **Dữ liệu:** SQLite WAL, upsert profile không xóa lịch sử, chỉ ghi event khi weight/profile cùng phiên; chuyển đơn vị kg/g/lb/oz; chống trùng dựa trên timestamp nguồn; tính tuổi BMI theo ngày địa phương khi cân; truy vấn lịch sử theo thời gian thực (tránh sai thứ tự bản ghi ISO có múi giờ khác nhau).
- **Services:** `add_measurement`, `update_measurement`, `delete_measurement`; schema kiểm tra đầu vào, lỗi rõ, action UI có Config Entry selector và Datetime selector.
- **Thẻ Dashboard:** Shadow DOM, visual editor, Sections `getGridOptions`, `getStubConfig`, chỉnh theme và nhiều entity, quản lý lịch sử, hành động thêm/sửa/xóa, cache discovery 5 giây, dấu hiệu lỗi đã escape HTML.
- **Bảo toàn dữ liệu:** đường dẫn DB giữ nguyên; không tự xóa profile/historical measurements khi gỡ entry; dữ liệu cũ được đọc sau migration, nhưng vẫn khuyến nghị sao lưu thư mục `<config>/smart_scale_bmi/` trước nâng cấp.
- **CI:** GitHub Actions chạy Python 3.14 unit tests, JavaScript syntax/API smoke tests, HACS/Marketplace validation, hassfest.

## Các phép thử đã chạy trong môi trường kiểm tra (không phải Home Assistant thật)

- Python `compileall` toàn bộ mã Python: **PASS**.
- `python -m pytest -q`: **10 passed**. Bao gồm schema SQLite cũ, giữ lịch sử khi đổi tên profile, tạo/sửa/xóa, chống trùng, trật tự múi giờ, tính BMI, thời điểm cân theo múi giờ, mock khởi tạo nền và ghi nhận trạng thái.
- `node --check custom_components/smart_scale_bmi/frontend/smart-scale-bmi-card.js`: **PASS**.
- `node tests/test_card.cjs`: **PASS**. Kiểm tra đăng ký custom element, Lovelace card methods, visual editor, HTML escaping, cache entity discovery.
- `unzip -t` bản phát hành: chạy khi đóng gói.

## Chưa thể xác nhận chỉ bằng kiểm thử tĩnh / mock

Chưa khởi chạy thử tích hợp trong một Home Assistant Core 2026.11.0 thật có browser, recorder và cân thông minh. Do đó **không thể cam kết tuyệt đối không có lỗi runtime hoặc lỗi UI do theme/custom cards khác**. GitHub Actions `hacs/action` và `hassfest` mới được **thêm workflow**, không có nghĩa đã chạy và đạt trên GitHub của người dùng.

## Checklist kiểm thử nghiệm thu trên Home Assistant

1. Sao lưu `<config>/smart_scale_bmi/` và tạo HA backup.
2. Cài repo dạng **Integration** trong Settings → Marketplace; đối chiếu metadata và Github Release tag `2026.11.0` với manifest.
3. Mở Settings → Devices & services → Smart Scale BMI → Add entry; cấu hình hai sensor cùng cân, kiểm tra `Options` và reload.
4. Restart Home Assistant, theo dõi thời gian startup và log `custom_components.smart_scale_bmi` mức debug; kiểm tra config entry loaded trước khi SQLite hoàn tất mà không chặn setup.
5. Thử cân lần lượt hai người; kiểm tra ID profile và weight đồng bộ trong 120 giây, không trùng, không ghép dữ liệu sai người; kiểm tra kg/lb và trạng thái unknown/unavailable.
6. Test add/update/delete qua Actions chọn Config Entry, rồi qua thẻ Dashboard; kiểm tra lịch sử hiển thị đúng ở chế độ Sections + Masonry, desktop + mobile, dark + light.
7. Kiểm tra `Settings → Dashboards → Resources`: chỉ một JS module `/smart_scale_bmi_ui/smart-scale-bmi-card.js`; trong môi trường YAML resource quản trị thủ công theo README.
8. Xuất Diagnostics; xác nhận không lộ tên/cân nặng/ngày sinh và số lịch sử khớp.
9. Chạy `hacs/action` và `hassfest` trên GitHub (workflow sẽ chạy sau push) và kiểm tra kết quả trước khi tạo Release chính thức.
