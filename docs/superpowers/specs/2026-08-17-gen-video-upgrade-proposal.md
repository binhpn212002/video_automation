# Đề xuất nâng cấp Gen Video

**Ngày:** 2026-08-17  
**Phạm vi:** ~10 TikTok account, lập lịch đăng tự động  
**Mục tiêu:** Pool video generate luôn đủ để lịch đăng không bị trống / delay

---

## 1. Bối cảnh hiện tại

Luồng Gen Video MVP:

1. Upload raw lên R2 (`video.library`)
2. Wizard **Generate Video**: tải video + audio → FFmpeg merge → upload lại R2
3. Đánh dấu `generated=True`, `state=available`
4. Lịch / đăng tay lấy video từ `video.library` (FIFO + chặn đăng trùng theo account)

Hạn chế chính với ~10 account:

- Gen **thủ công**, từng video
- Gen **ghi đè** `storage_path` của cùng record (1 raw ≈ 1 output)
- Chọn nhạc **random** thuần
- Gen **đồng bộ** trên request Odoo (dễ timeout khi nhiều)
- Chưa có **cảnh báo / top-up** khi pool sắp hết

Với quy mô 10 account, nút thắt không phải TikTok API mà là **pool video đã generate sẵn**.

---

## 2. Mục tiêu vận hành

Giả sử mỗi account có **N slot/ngày**.

**Công thức buffer khuyến nghị:**

```text
Pool sẵn ≈ số_account × N × (3..5 ngày)
```

Ví dụ: 10 account × 2 slot/ngày → giữ **60–100** video:

- `generated = True`
- `state = available`
- chưa có `tiktok.upload.history` success với account sẽ đăng

Nhờ đó cron lịch luôn có hàng FIFO, không phụ thuộc người ngồi bấm Generate.

---

## 3. Đề xuất nâng cấp (theo ROI)

### 3.1. Auto top-up pool — ưu tiên #1

**Làm gì**

- Cron (sáng / trước khung giờ đăng) đếm video chưa dùng theo bucket / account pool
- Nếu dưới ngưỡng → auto gen từ raw còn lại

**Vì sao**

- 10 account không gen tay từng cái được ổn định
- Đăng không phụ thuộc operator online

**Done khi**

- Có cấu hình `min_pool_size` (global hoặc theo storage/account)
- Cron tạo đủ video `available` trước giờ publish

---

### 3.2. Gen tạo bản mới, không ghi đè

**Làm gì**

- 1 raw → nhiều output (nhạc khác / logo khác)
- Mỗi output là record `video.library` riêng (hoặc link `parent_video_id`)
- Giữ nguyên file gốc trên R2

**Vì sao**

- 1 raw phục vụ nhiều account mà không “đốt” video gốc
- Tăng tốc nhân bản nội dung hợp lệ

**Done khi**

- Generate tạo record mới + `generated=True`
- Raw gốc vẫn xem / gen lại được

---

### 3.3. Ghép nhạc thông minh hơn random

**Làm gì**

- Rotate audio theo usage count
- Tránh cùng audio lặp gần đây trên cùng account / cùng ngày
- (Optional) gắn audio set theo niche/account

**Vì sao**

- Random thuần dễ trùng cảm giác giữa các account
- Giảm rủi ro bị nhận diện spam

**Done khi**

- Có log `audio_id` + lần dùng gần nhất
- Auto pick loại audio đã dùng gần đây cho cùng account

---

### 3.4. Preset theo account / niche

**Làm gì**

- Logo, tỉ lệ 9:16, volume nhạc, watermark gắn `tiktok.account` hoặc schedule rule
- Wizard/cron đọc preset khi gen

**Vì sao**

- 10 account khác brand/niche
- Gen một kiểu cho tất cả dễ đồng nhất quá mức

**Done khi**

- Account có preset gen
- Output phản ánh đúng preset

---

### 3.5. Gen async (job queue)

**Làm gì**

- Wizard chỉ tạo job `pending`
- Cron/worker chạy FFmpeg + upload R2
- UI theo dõi state: pending / processing / done / failed

**Vì sao**

- Gen sync trên HTTP dễ timeout
- 10 account × nhiều clip đụng CPU cùng lúc với cron publish

**Done khi**

- Generate không block UI > vài giây
- Fail có retry + `error_message` rõ

---

### 3.6. Cảnh báo “đủ hàng trước ngày đăng”

**Làm gì**

- Dashboard / smart button trên schedule rule:
  - Ngày mai cần X slot
  - Pool còn Y
  - Y < X → cảnh báo đỏ + activity/message

**Vì sao**

- Biết sớm thiếu để gen bù trước khi cron tạo queue

**Done khi**

- Operator thấy thiếu pool trước ≥ 1 ngày đăng

---

### 3.7. Chuẩn encode TikTok

**Làm gì**

- Validate / encode theo giới hạn TikTok (duration, H.264, fps, resolution, max size)
- Fail sớm lúc gen thay vì lúc FILE_UPLOAD

**Vì sao**

- Fail lúc upload tốn token thời gian và khó debug hơn fail lúc gen

**Done khi**

- Video fail spec không vào `available`

---

## 4. Luồng vận hành đề xuất (đủ cho 10 account)

```text
Upload raw → R2
      ↓
Đêm / sáng: Auto gen (top-up buffer)
      ↓
Pool: generated + available + chưa đăng (theo account)
      ↓
Cron lịch FIFO → Publish Queue → FILE_UPLOAD Inbox
      ↓
History success → video không dùng lại cho account đó
```

Nguyên tắc:

1. **Gen trước, đăng sau** — luôn dư buffer 3–5 ngày  
2. **Gen chỉ khi dưới ngưỡng** — không gen thừa vô hạn  
3. **Publish không chờ gen** — hai pipeline tách nhau  

---

## 5. Thứ tự triển khai đề xuất

| Phase | Việc | Effort ước lượng | Impact |
|------|------|------------------|--------|
| P1 | Ngưỡng pool + cảnh báo thiếu | Nhỏ | Cao |
| P2 | Auto top-up cron | Trung bình | Rất cao |
| P3 | Gen ra record mới (không overwrite) | Trung bình | Cao |
| P4 | Audio rotate thông minh | Nhỏ–TB | Trung bình |
| P5 | Preset theo account | Trung bình | Trung bình |
| P6 | Gen async job | Lớn hơn | Cao khi volume tăng |
| P7 | Encode validate TikTok | Nhỏ–TB | Trung bình |

Khuyến nghị bắt đầu: **P1 → P2 → P3**.

---

## 6. Việc chưa cần làm sớm (với ~10 account)

- Worker microservice / K8s riêng
- AI caption / gen scene phức tạp
- Chiến lược chọn video random/priority phức tạp thay FIFO
- Multi-platform (Shorts/Reels) trước khi pool TikTok ổn

FIFO + buffer + auto top-up là đủ ổn định ở quy mô này.

---

## 7. Metric theo dõi

- Số video `available` + `generated` chưa đăng (theo bucket)
- Số slot lịch ngày mai vs pool còn lại
- Tỷ lệ slot trống do thiếu video
- Thời gian trung bình 1 job gen
- Tỷ lệ gen fail / upload fail

---

## 8. Quyết định cần chốt trước khi code

1. Buffer bao nhiêu ngày? (đề xuất mặc định **3 ngày**)
2. Gen overwrite hay luôn tạo record mới? (đề xuất **record mới**)
3. Top-up theo **global bucket** hay **theo từng account**?
4. Gen chạy sync tiếp tục hay chuyển async ngay từ P2?

---

## 9. Liên quan module hiện có

| Thành phần | Vai trò |
|------------|---------|
| `video.library` | Raw + generated |
| `audio.library` | Nhạc merge |
| `video.generate.wizard` | Gen thủ công hiện tại |
| `tiktok.schedule.rule` | Slot đăng / ngày |
| `tiktok.publish.queue` | Hàng đợi đăng |
| `tiktok.upload.history` | Chặn đăng trùng 1 video / 1 account |

Nâng cấp Gen Video phải tôn trọng rule: **1 account chỉ đăng 1 video thành công đúng 1 lần**.
