# TikTok Affiliate Video Generator — Technical Specification & Module Design

**Tài liệu:** Thiết kế chi tiết tính năng TikTok Affiliate Video Generator (1 Ảnh + 1 MP3) & Auto Gen Queue  
**Tích hợp:** Module `video_automation` (Odoo 17 / Cloudflare R2 / FFmpeg / TikTok Content Posting)  
**Ngày cập nhật:** 2026-08-22  
**Trạng thái:** Sẵn sàng triển khai (Ready for Implementation)  

---

## 1. Mục tiêu & Nguyên tắc cốt lõi

### 1.1. Mục tiêu
Tự động hóa hoàn toàn quy trình sản xuất video TikTok Affiliate chất lượng cao (chuẩn 9:16, 1080×1920, 30 FPS) từ:
- **01 Ảnh sản phẩm** (JPG/PNG/WEBP) hoặc ảnh người mẫu mặc sản phẩm trong danh sách ảnh chờ gen.
- **01 File âm thanh MP3** (lấy từ thư viện nhạc `audio.library` theo cơ chế xoay tua).
- **Hook & Call To Action (CTA) Text** xuất hiện đúng vị trí TikTok Safe Area.

**Yêu cầu nghiệp vụ đặc biệt (Auto Gen Queue):**
> **Hệ thống lưu trữ danh sách hình ảnh sản phẩm (`product.image`) và CHỈ tự động generate video cho những ảnh CHƯA ĐƯỢC GEN (`generated = False`). Mỗi ảnh sản phẩm sẽ được tạo video đúng 1 lần (FIFO) và tự động chuyển trạng thái để không bao giờ bị gen trùng lặp.**

Video sau khi render sẽ tự động đổ về **`video.library`** (được đánh dấu `generated=True`, `state='available'`) để sẵn sàng cấp nguồn cho hệ thống **Auto Top-up Pool** và **Hàng đợi đăng TikTok (`tiktok.publish.queue`)**.

### 1.2. Nguyên tắc bất di bất dịch (Core Principles)
1. **Không can thiệp cấu trúc sản phẩm:** Tuyệt đối KHÔNG `crop`, `zoom`, `pan`, `stretch`, `rotate`, `warp` ảnh foreground. Toàn bộ ảnh sản phẩm được giữ nguyên vẹn 100% bằng cơ chế `scale contain`.
2. **Quản lý trạng thái ảnh chặt chẽ:** Chỉ gen ảnh ở trạng thái `uploaded` và `generated=False`. Sau khi gen thành công, cập nhật ngay `generated=True` và liên kết với video đầu ra.
3. **Không phụ thuộc AI nhận diện:** Không sử dụng Object Detection, Segmentation hay AI model nặng nề.
4. **Tạo cảm giác chuyển động bằng Ánh sáng & Nhịp điệu:** Video sống động nhờ sự kết hợp giữa **Blurred Background**, hiệu ứng **Beat Pulse** (Brightness/Contrast/Saturation pulse) và **White Flash** đồng bộ chính xác với beat của file MP3.
5. **Xử lý FFmpeg 1-Pass:** Toàn bộ quá trình chuẩn hóa background, contain foreground, chèn beat pulse, flash và text overlay được thực hiện trong **duy nhất 1 Filter Complex**, không re-encode nhiều lần.
6. **Không bao giờ fail vì audio:** Nếu beat detection không tìm được nhịp, hệ thống tự động kích hoạt **Fallback Adaptive Interval** (500–700ms).

---

## 2. Kiến trúc luồng xử lý & Auto Gen Queue

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        KHO ẢNH SẢN PHẨM (product.image)                │
│  - Danh sách ảnh sản phẩm lưu trên Cloudflare R2                       │
│  - Lọc ứng viên: active=True, generated=False, state='uploaded' (FIFO) │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼ (Chọn 1 ảnh chưa gen)
┌─────────────────────────┐         │         ┌─────────────────────────┐
│   product.image (R2)    │ ────────┤         │   audio.library (R2)    │
│  (State: 'generating')  │                   │ (Chọn bài nhạc ít dùng) │
└─────────────────────────┘                   └────────────┬────────────┘
                                                           │
                                                           ▼
                                              ┌─────────────────────────┐
                                              │  Audio Signal Analysis  │
                                              │  (Onset/Beat Detection) │
                                              │  → Cache beat_data      │
                                              └────────────┬────────────┘
                                                           │
                                                           ▼
    ┌────────────────────────────────────────────────────────────────┐
    │                  FFmpeg 1-Pass Filter Complex                  │
    │  1. Background: Scale cover 1080x1920 + BoxBlur (r=25)         │
    │  2. Foreground: Scale contain + Center overlay                 │
    │  3. Beat Pulse: Dynamic Brightness/Contrast timeline           │
    │  4. Flash: White flash overlay tại beat timestamps             │
    │  5. Hook: Top text (0s → 2.5s) kèm Fade                        │
    │  6. CTA: Bottom safe area (cuối video)                         │
    │  7. Mux Audio AAC + Video H.264 (1080x1920, 30fps)            │
    └───────────────────────────────┬────────────────────────────────┘
                                    │
                                    ▼
    ┌────────────────────────────────────────────────────────────────┐
    │                     Upload Cloudflare R2                       │
    │        Object Key: g/g_<hash>_<id>.mp4 → CDN URL               │
    └───────────────────────────────┬────────────────────────────────┘
                                    │
                                    ▼
    ┌────────────────────────────────────────────────────────────────┐
    │                Cập nhật Trạng thái & Tạo Video                 │
    │  1. Tạo record video.library: generated=True, state='available'│
    │  2. Cập nhật product.image: generated=True, state='generated'  │
    └───────────────────────────────┬────────────────────────────────┘
                                    │
                                    ▼
    ┌────────────────────────────────────────────────────────────────┐
    │           Sẵn sàng cho Auto Schedule & TikTok Post             │
    │     (tiktok.schedule.rule → tiktok.publish.queue → Inbox)      │
    └────────────────────────────────────────────────────────────────┘
```

---

## 3. Data Schema & Models trong Module `video_automation`

### 3.1. Model: `product.image` (Quản lý kho ảnh & Trạng thái Auto Gen)
Lưu trữ danh sách ảnh sản phẩm Affiliate, kiểm soát việc sinh video tự động.

| Tên trường (Field) | Kiểu dữ liệu | Mô tả |
| :--- | :--- | :--- |
| `name` | `fields.Char(required=True, tracking=True)` | Tên sản phẩm / Mã SKU / Tiêu đề ảnh |
| `storage_id` | `fields.Many2one('video.storage', required=True)` | Cấu hình R2 Storage |
| `storage_path` | `fields.Char()` | Object key trên R2 (ví dụ: `img/p_123.jpg`) |
| `cdn_url` | `fields.Char(compute='_compute_cdn_url', store=True)` | Public CDN URL xem trước ảnh |
| `upload_file` | `fields.Binary(attachment=False)` | Upload file từ thiết bị (JPG/PNG/WEBP) |
| `upload_filename`| `fields.Char()` | Tên file upload gốc |
| `width`, `height`| `fields.Integer()` | Kích thước ảnh gốc (px) |
| `file_size` | `fields.Integer()` | Dung lượng file (bytes) |
| `state` | `fields.Selection` | Trạng thái: `draft`, `uploaded`, `generating`, `generated`, `failed` |
| `generated` | `fields.Boolean(default=False, index=True, tracking=True)` | **Cờ đánh dấu đã tạo video hay chưa** |
| `allow_multiple_gen` | `fields.Boolean(default=False)` | Nếu False: chỉ gen đúng 1 lần; nếu True: cho phép gen nhiều lần |
| `generated_video_ids` | `fields.One2many('video.library', 'source_image_id')` | Danh sách video thành phẩm được tạo từ ảnh này |
| `generated_video_count`| `fields.Integer(compute='_compute_video_count', store=True)` | Số lượng video đã tạo |
| `last_generated_date` | `fields.Datetime()` | Thời điểm tạo video thành công gần nhất |
| `last_error` | `fields.Text()` | Chi tiết lỗi nếu quá trình gen thất bại |
| `default_hook` | `fields.Char()` | Hook text mặc định (VD: *"Mẫu này đang cực hot 🔥"*) |
| `default_cta` | `fields.Char()` | CTA text mặc định (VD: *"Xem sản phẩm bên dưới ↓"*) |
| `active` | `fields.Boolean(default=True)` | Trạng thái sử dụng (Archive/Unarchive) |

#### Logic Lọc ứng viên Auto Gen (`_pending_image_candidates`):
```python
@api.model
def _pending_image_candidates(self, storage, limit=None):
    """
    Lấy danh sách các ảnh sản phẩm CHƯA TỪNG ĐƯỢC GEN (generated=False)
    theo thứ tự FIFO (ảnh upload trước được tạo video trước).
    """
    domain = [
        ("storage_id", "=", storage.id),
        ("active", "=", True),
        ("storage_path", "!=", False),
        ("generated", "=", False),
        ("state", "in", ("uploaded", "failed")),
    ]
    return self.search(domain, order="create_date asc, id asc", limit=limit)
```

---

### 3.2. Cập nhật Model: `audio.library`
Bổ sung cơ chế lưu trữ kết quả phân tích Beat để tái sử dụng:

| Tên trường (Field) | Kiểu dữ liệu | Mô tả |
| :--- | :--- | :--- |
| `beat_data` | `fields.Text()` | JSON Array lưu danh sách timestamp nhịp (VD: `[0.52, 1.04, 1.56, ...]`) |
| `bpm` | `fields.Float()` | Nhịp đập mỗi phút ước tính |
| `beat_status` | `fields.Selection(['none', 'detected', 'fallback'])` | Trạng thái phân tích beat |

**Methods:**
- `action_analyze_beats()`: Phân tích tín hiệu âm thanh và cache timestamps.
- `get_or_compute_beats(storage)`: Lấy timestamps từ cache hoặc tính toán tức thì.

---

### 3.3. Cập nhật Model: `video.library`
Bổ sung liên kết nguồn gốc Affiliate Image:

| Tên trường (Field) | Kiểu dữ liệu | Mô tả |
| :--- | :--- | :--- |
| `source_type` | `fields.Selection([('raw_video', 'Raw Video'), ('affiliate_image', 'Affiliate Image')])` | Phân loại nguồn tạo video |
| `source_image_id`| `fields.Many2one('product.image', ondelete='set null')` | Liên kết đến ảnh sản phẩm gốc |
| `hook_text` | `fields.Char()` | Hook text đã áp dụng |
| `cta_text` | `fields.Char()` | CTA text đã áp dụng |
| `effect_preset` | `fields.Selection([('soft', 'Soft'), ('normal', 'Normal'), ('strong', 'Strong')])` | Cường độ hiệu ứng beat pulse |

> **Kế thừa hoàn toàn:** Các trường `storage_id`, `storage_path`, `cdn_url`, `duration`, `width`, `height`, `fps`, `state`, `generated`, `is_posted` giữ nguyên để đảm bảo 100% khả năng tương thích với luồng lập lịch `tiktok.publish.queue`.

---

### 3.4. Cập nhật Model: `video.storage` (Thống kê Pool & Auto Top-up từ Kho Ảnh)
Bổ sung theo dõi số lượng ảnh chờ gen trên từng Storage:

| Tên trường (Field) | Kiểu dữ liệu | Mô tả |
| :--- | :--- | :--- |
| `pool_images_pending_count` | `fields.Integer(compute='_compute_pool_stats')` | Số lượng ảnh **chưa gen** trong kho (`generated=False`) |
| `pool_images_generated_count`| `fields.Integer(compute='_compute_pool_stats')` | Số lượng ảnh **đã gen** (`generated=True`) |
| `auto_gen_from_images` | `fields.Boolean(default=True)` | Tự động sinh video từ ảnh khi thiếu pool |

#### Cơ chế Auto Top-up (`action_top_up_pool`):
Khi tổng số video sẵn sàng (`pool_available_count`) < số lượng cần thiết (`pool_needed`):
1. Ưu tiên 1: Lấy từ Raw Videos chưa dùng.
2. Ưu tiên 2: Lấy từ **Ảnh sản phẩm chưa gen (`_pending_image_candidates`)**.
3. Mỗi ảnh chưa gen sẽ được ghép với 1 bản nhạc ít dùng nhất (`_pick_audio`), render thành video 9:16, tạo record `video.library` và cập nhật ảnh sang `generated = True`.

---

## 4. Đặc tả FFmpeg Rendering Engine (`services/ffmpeg_service.py`)

### 4.1. Thông số Kỹ thuật Chuẩn TikTok Output
```text
Container: MP4
Video Codec: libx264
Audio Codec: aac (192 kbps, stereo, 44.1kHz / 48kHz)
Resolution: 1080x1920 (Aspect ratio 9:16)
Frame Rate: 30 FPS
Pixel Format: yuv420p
Preset: veryfast
CRF: 22
Duration: Bằng chính xác độ dài file MP3 (-shortest hoặc -t audio_duration)
```

### 4.2. Filtergraph Pipeline Cấu trúc
Filter complex bao gồm 5 tầng xử lý song song và nối tiếp:

```text
[0:v] ──┬──> scale=1080:1920:increase, crop=1080:1920, boxblur=25:2 ──────────> [bg] ──┐
        └──> scale=1080:1920:decrease ────────────────────────────────────────> [fg] ──┴──> overlay=(W-w)/2:(H-h)/2 [base]
                                                                                                      │
                                                                                                      ▼
                                                             [base] ──> eq=brightness=...:contrast=... [pulsed]
                                                                                                      │
                                                                                                      ▼
                                                             [pulsed] ──> drawbox (White Flash tại beats) [flashed]
                                                                                                      │
                                                                                                      ▼
                                                             [flashed] ──> drawtext (Hook top, 0-2.5s) [hooked]
                                                                                                      │
                                                                                                      ▼
                                                             [hooked] ──> drawtext (CTA bottom safe area) [vout]
```

### 4.3. Cấu hình Preset Hiệu ứng Beat Pulse

| Preset | Độ tăng Brightness (`b`) | Tỷ lệ Contrast (`c`) | Flash Opacity | Độ dài hiệu ứng (Duration) |
| :--- | :--- | :--- | :--- | :--- |
| **Soft** | `+0.03` | `1.02` | `0.05` | 80 ms |
| **Normal** (Mặc định) | `+0.07` | `1.05` | `0.10` | 100 ms |
| **Strong** | `+0.12` | `1.08` | `0.15` | 130 ms |

### 4.4. TikTok Safe Area UI Giới hạn Text
- **Hook Text (Đầu video):**
  - Vị trí: `x=(w-text_w)/2`, `y=260` (Cách mép trên ~260px, tránh thanh tìm kiếm và header TikTok).
  - Thời gian: `between(t, 0, 2.5)` (Xuất hiện 2.5 giây đầu).
  - Font Style: Size 58–64, Màu Trắng `#FFFFFF`, Border Viền Đen 4px để nổi bật trên mọi nền ảnh.
- **CTA Text (Cuối video):**
  - Vị trí: `x=(w-text_w)/2`, `y=1580` (Cách mép dưới ~340px, tránh Caption, Music Bar và nút Tương tác bên phải).
  - Thời gian: `between(t, duration - 4.0, duration)` (Xuất hiện 4 giây cuối).
  - Font Style: Size 52–56, Màu Trắng `#FFFFFF`, Border Viền Đen 4px.

---

## 5. Thuật toán Beat Detection & Fallback Strategy

### 5.1. Thuật toán Onset Energy Detection (Sử dụng `numpy` + `ffmpeg`)
1. Dùng `ffmpeg` giải mã nhanh audio thành raw PCM mono 22050Hz qua stdout.
2. Chia tín hiệu thành các frame cửa sổ (Window Size = 1024 mẫu, Hop Size = 512 mẫu ≈ 23ms).
3. Tính Root Mean Square (RMS) Energy cho từng frame.
4. Xác định **Adaptive Threshold**:
   $$\text{Threshold}(i) = \mu_{\text{local}}(i) + 1.5 \times \sigma_{\text{local}}(i)$$
5. Ghi nhận Beat Timestamp khi:
   - Energy tại frame $i$ là cực đại địa phương (Peak).
   - $\text{Energy}(i) > \text{Threshold}(i)$.
   - Khoảng cách từ beat gần nhất $\ge 200\text{ ms}$ (ngăn ngừa giật quá nhanh).

### 5.2. Fallback Mechanism (Không bao giờ fail)
- Nếu số lượng beat phát hiện $< 3$ (hoặc âm thanh không có nhịp rõ ràng):
  - Kích hoạt **Fallback Fixed Pulse Interval**: Tự động tạo beat cố định cách nhau mỗi **$0.6\text{s}$** (600ms).
  - Trạng thái `beat_status` của audio được ghi là `'fallback'`.

---

## 6. Batch Upload & Giao diện Quản lý Danh sách Ảnh

### 6.1. Bulk Upload Wizard (`product.image.bulk.wizard`)
- Cho phép người dùng chọn cùng lúc nhiều file ảnh (10–100 ảnh) từ thiết bị.
- Tự động tạo hàng loạt record `product.image`, đẩy file lên Cloudflare R2, gán `state='uploaded'`, `generated=False`.

### 6.2. Action "Auto Gen từ danh sách ảnh" (Manual Trigger / Batch Action)
- Cho phép chọn nhiều ảnh trên danh sách (Tree View) và bấm **"Tạo Video TikTok"**.
- Hệ thống chỉ thực thi trên những ảnh có `generated=False`. Ảnh đã tạo rồi sẽ được bỏ qua kèm thông báo rõ ràng cho người dùng.

---

## 7. REST API Specification

### 7.1. Endpoint Tạo Job Render Video
- **URL:** `POST /api/video/create`
- **Authentication:** Odoo User Session / API Key
- **Request Body:**
```json
{
  "image_id": 15,
  "audio_id": 8,
  "storage_id": 1,
  "background": {
    "type": "blur"
  },
  "effect": {
    "preset": "normal",
    "flash": true
  },
  "text": {
    "hook": {
      "text": "Mẫu áo này đang rất hot 🔥"
    },
    "cta": {
      "text": "Xem sản phẩm bên dưới ↓"
    }
  }
}
```
- **Response (200 OK):**
```json
{
  "status": "processing",
  "job_id": "aff_9e4a1b82",
  "message": "Video creation job has been queued."
}
```

---

### 7.2. Endpoint Tra cứu Tiến trình Job
- **URL:** `GET /api/video/jobs/<string:job_id>`
- **Response (Đang xử lý):**
```json
{
  "job_id": "aff_9e4a1b82",
  "status": "processing",
  "stage": "RENDERING"
}
```
- **Response (Hoàn thành):**
```json
{
  "job_id": "aff_9e4a1b82",
  "status": "completed",
  "stage": "COMPLETED",
  "video_id": 104,
  "video_url": "https://pub-xxxx.r2.dev/g/g_aff_9e4a1b82.mp4",
  "duration": 15.4,
  "width": 1080,
  "height": 1920,
  "fps": 30
}
```

---

## 8. Kế hoạch triển khai (Roadmap)

| Giai đoạn | Nội dung công việc | File liên quan | Ước tính |
| :--- | :--- | :--- | :--- |
| **Phase 1** | Tạo Model `product.image` (kèm trạng thái `generated`, FIFO queue) & Bulk Upload | `models/product_image.py`, `wizard/product_image_bulk_wizard.py` | 1 ngày |
| **Phase 2** | Triển khai Onset Beat Detection & Hàm FFmpeg 1-Pass Filtergraph | `services/ffmpeg_service.py` | 1.5 ngày |
| **Phase 3** | Cập nhật `video.library`, Nâng cấp `video.generate.wizard` | `models/video_library.py`, `wizard/video_generate_wizard.py` | 1 ngày |
| **Phase 4** | Xây dựng `video.generate.job` & REST API Controller | `models/video_generate_job.py`, `controllers/affiliate_api.py` | 1 ngày |
| **Phase 5** | Nối luồng Auto Top-up Pool từ kho ảnh chưa gen & Kiểm thử trên TikTok | `models/video_storage.py`, Test suite | 1 ngày |
