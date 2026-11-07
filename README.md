# Smart Scale BMI — Home Assistant Marketplace / HACS

Tích hợp **Smart Scale BMI** cho Home Assistant, theo dõi BMI từng người, ghi lịch sử cân nặng trong SQLite, cung cấp sensor, ba action và một thẻ Dashboard (Lovelace) có trình chỉnh sửa đồ họa.

**Phiên bản:** 2026.11.1 • **Home Assistant tối thiểu:** 2026.11.0 • Không yêu cầu thư viện Python bổ sung.

## Cài đặt với Marketplace (Home Assistant 2026.11 trở lên)

[![Open repository in Home Assistant Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=khaisilk1910&repository=smart_scale_bmi&category=integration)

1. Vào **Settings → Marketplace → Custom repositories**; kết nối GitHub (nếu được yêu cầu), rồi nhập `https://github.com/khaisilk1910/smart_scale_bmi` và chọn loại **Integration**. Với repository đã có trong catalog, chỉ cần tìm tên và cài.
2. Tại **Settings → Devices & services → Add integration**, tìm **Smart Scale BMI**.
3. Mỗi người cân tạo một entry, chọn **sensor cân nặng**, **sensor profile ID**, **ID profile**, tên, tháng/năm sinh, giới tính và chiều cao.
4. Mặc định **cân nặng khởi tạo = 0**: không tạo lần cân giả. Nếu nhập giá trị lớn hơn 0, tích hợp lưu **một lần cân khởi tạo** chỉ khi hồ sơ chưa có lịch sử.
5. Vào Dashboard → Edit dashboard → Add card → **Smart Scale BMI Card**. Thẻ tự tìm BMI sensors, hỗ trợ tabs, biểu đồ, lịch sử, thêm/sửa/xóa lần cân và chỉnh màu.

Khi cập nhật tích hợp đã tải trong bộ nhớ, Home Assistant thường yêu cầu **restart**. Sau đó tải lại giao diện trình duyệt (hard refresh nếu tài nguyên cũ còn nằm trong cache).

### Thẻ không xuất hiện trong Dashboard?

Tích hợp thường tự đăng ký resource `/smart_scale_bmi_ui/smart-scale-bmi-card.js?v=2026.11.1`. Nếu quản lý `lovelace.resources` bằng YAML hoặc resource tự động chưa sẵn sàng, thêm vào:

```yaml
lovelace:
  resource_mode: yaml
  resources:
    - url: /smart_scale_bmi_ui/smart-scale-bmi-card.js?v=2026.11.1
      type: module
```

**Chỉ thêm một lần**; không vừa thêm YAML resource vừa thêm Dashboard resource thủ công, tránh tải JS trùng lặp. Nếu dashboard ở chế độ UI, kiểm tra mục **Settings → Dashboards → Resources** và sửa URL nếu cần.

Thẻ YAML ví dụ:

```yaml
type: custom:smart-scale-bmi-card
auto_entities: true
show_tabs: true
show_charts: true
show_table: true
max_records: 60
```

Khi không muốn tự tìm, đặt `auto_entities: false` và `entities: [sensor.your_bmi_sensor]` hoặc nhập entity IDs trong trình chỉnh sửa thẻ. Thẻ hoạt động cả chế độ Sections và Masonry.

## Logic thu thập cân nặng

- Nghe thay đổi của hai sensor đã chọn; đợi **2 giây** để sự kiện cân và profile ổn định.
- Chỉ ghi dữ liệu khi ID profile trùng và **cả hai sensor có timestamp thay đổi trong vòng 120 giây**. Đây là quy tắc chủ động chống ghép cân của người khác. Một số cân không cập nhật lại profile ID nếu ID không đổi; nếu vậy bạn cần cấu hình thiết bị/automation để profile sensor phát trạng thái mới mỗi phiên cân. Cặp trạng thái quá cũ bị bỏ qua, ghi lý do ở log DEBUG.
- Nhận `kg`, `g`, `lb/lbs`, `oz`; chuyển về kg trước khi tính BMI; chặn NaN/Infinity và cân ngoài 1–300 kg.
- Mỗi cặp sự kiện cảm biến chỉ được lưu một lần, kể cả sau khi reload nếu timestamp nguồn không đổi. Thông tin lịch sử gốc vẫn nằm ở SQLite; 60 lần gần nhất được gửi vào thuộc tính sensor để giảm tải giao diện và recorder.
- BMI cho người lớn theo ngưỡng WHO; biểu đồ WHO 2007 cho 5–19 tuổi. **Dưới 5 tuổi hiện chưa có bảng WHO 0–5 tuổi**, nên cảnh báo chỉ mang tính thông tin, không phải chẩn đoán.

## Actions

Trong **Developer Tools → Actions**, trường `entry_id` dùng bộ chọn **Config Entry** để chọn đúng người cân (không cần chép ID thủ công):

| Action | Trường bắt buộc | Trường tùy chọn |
| --- | --- | --- |
| `smart_scale_bmi.add_measurement` | `entry_id`, `weight_kg` | `measured_at` (ISO 8601, mặc định giờ hiện tại) |
| `smart_scale_bmi.update_measurement` | `entry_id`, `measurement_id`, `weight_kg` | — |
| `smart_scale_bmi.delete_measurement` | `entry_id`, `measurement_id` | — |

Giá trị cân hợp lệ **1–300 kg**. Khi sửa lần cân, BMI và phân loại được tính lại theo **ngày cân gốc**, không dùng tuổi hiện tại. Nếu action lỗi, Home Assistant báo lỗi rõ ràng, thay vì bỏ qua âm thầm.

## Dữ liệu & hiệu năng

- Database: `<config>/smart_scale_bmi/smart_scale_bmi.db` — **nằm ngoài `custom_components/`**, nên cập nhật Marketplace không xóa lịch sử. Sao lưu thư mục này trước khi nâng cấp.
- Dùng SQLite WAL và các index hỗ trợ truy vấn; tất cả truy cập file/SQLite đều qua executor, không chạy trên event loop.
- Tác vụ SQLite chạy nền theo vòng đời config entry, không cản quá trình HA khởi động. **Tất cả các profile chia sẻ một lần tạo/migrate database** trên mỗi lượt chạy HA; truy cập ghi giữa các executor được tuần tự hóa để tránh tranh chấp lock, truy cập đọc vẫn có thể chạy song song. Nếu SQLite tạm khóa từ tiến trình bên ngoài, tích hợp sẽ retry 3 lần, chờ 5 giây giữa các lần và chỉ ghi traceback khi lỗi thật sự chưa phục hồi.
- Có thể tải **Diagnostics** trong cấu hình tích hợp: chỉ báo trạng thái, loại lỗi và số bản ghi, không xuất tên, cân nặng hay ngày sinh.

## Debug

Vào **Settings → Devices & services → Smart Scale BMI → Enable debug logging**, hoặc cấu hình:

```yaml
logger:
  logs:
    custom_components.smart_scale_bmi: debug
```

Trong log tìm các thông điệp: `SQLite temporarily busy during background initialization`, `SQLite initialization failed`, `Skipped stale scale pair`, `profile mismatch`, `Scale measurement stored`, `Duplicate scale event ignored`, hoặc `Cannot register Smart Scale BMI dashboard card resource`. Khi chia sẻ log, kiểm tra và ẩn tên sensor/hồ sơ nếu cần.

## Xử lý `sqlite3.OperationalError: database is locked`

Từ **2026.11.1**, tích hợp không còn thực hiện `PRAGMA journal_mode=WAL` đồng thời cho từng hồ sơ. WAL đã bật sẽ được giữ nguyên qua các lần khởi động và chỉ được thiết lập lại khi cần. Nếu có ứng dụng khác giữ khóa trong lúc HA bật, worker sẽ chờ và thử lại; dashboard/sensor không chờ khởi tạo SQLite trên event loop.

1. Sao lưu đầy đủ `<config>/smart_scale_bmi/` bằng chức năng Backup của Home Assistant trước khi nâng cấp.
2. Cài bản `2026.11.1`, restart Home Assistant và xác nhận các hồ sơ cân đã có trạng thái sensor. Khi chỉ thấy cảnh báo `SQLite temporarily busy` rồi `SQLite ready`, dữ liệu đã được khôi phục bình thường.
3. Nếu ba lần vẫn lỗi, kiểm tra **quyền ghi thư mục**, dung lượng đĩa, việc có đồng thời **một bản Home Assistant khác / công cụ SQLite / backup** đang mở file `<config>/smart_scale_bmi/smart_scale_bmi.db`, và việc file nằm trên **ổ cục bộ** (WAL không phù hợp với NFS/SMB không hỗ trợ shared-memory locking). Dừng tiến trình giữ khóa, rồi reload integration hoặc restart Home Assistant.
4. **Không xóa database**, không xóa `*.db-wal` hoặc `*.db-shm` khi Home Assistant đang chạy vì có thể mất giao dịch chưa checkpoint. Nếu vẫn lỗi hãy chia sẻ toàn bộ lỗi gần nhất sau 3 lần retry, cùng kiểu lưu trữ của `/config`, **không gửi dữ liệu cân cá nhân**.

## Phát hành

Marketplace tiếp tục dùng `hacs.json`, phân phối loại **Integration** và yêu cầu `manifest.json` có version hợp lệ. Khi tạo GitHub Release, đặt tag/version trùng `manifest.json` (ví dụ `2026.11.1`); **không sửa tag sau khi phát hành**. Repository có workflows `hacs/action` và `hassfest` để kiểm tra trước release. Xem thêm [`VERIFICATION.md`](VERIFICATION.md) về kiểm thử và checklist nghiệm thu trên Home Assistant thật. Repository này không phải plugin Dashboard độc lập: thẻ JavaScript được **đóng gói bên trong integration** và tích hợp tự phục vụ trên URL riêng.

Automation thông báo mẫu: [`automation/Smart Scale - Thong bao BMI theo nguoi can.yaml`](automation/Smart%20Scale%20-%20Thong%20bao%20BMI%20theo%20nguoi%20can.yaml). Cần chỉnh entity IDs/dịch vụ thông báo theo hệ thống của bạn.

![Smart Scale BMI card](https://github.com/user-attachments/assets/757e5f47-7646-4368-a2a2-87023c16f9cb)

---

**Compatibility note:** `hacs.json` remains required by the built-in Marketplace in Home Assistant 2026.11+. This repository is published as an **integration** with an embedded custom dashboard card, not as two separately installable Marketplace packages. Runtime checks against a real Home Assistant installation are recommended before production rollout.
