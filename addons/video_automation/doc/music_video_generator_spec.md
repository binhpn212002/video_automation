# Music Video Generator (Tạo Video Ca Nhạc) — Technical Specification & Module Design

**Tài liệu:** Thiết kế chi tiết tính năng Tạo Video Ca Nhạc từ Ảnh Background + Ảnh Nhân Vật + File Nhạc (`video_type = 'music_video'`)  
**Tích hợp:** Module `video_automation` (Odoo 17 / Cloudflare R2 / FFmpeg / Audio Signal Processing / TikTok Publishing)  
**Ngày cập nhật:** 2026-08-30  
**Trạng thái:** Sẵn sàng phát triển (Ready for Implementation)  

---

## 1. Tổng quan & Mục tiêu Nghiệp vụ

### 1.1. Bối cảnh
Trong sản xuất video âm nhạc ngắn (TikTok, YouTube Shorts, Reels), phong cách **Music Visualizer thuần Visual / Nghệ thuật** (không chèn chữ thông tin hay phụ đề phức tạp) giúp video trở nên tinh tế, tập trung trọn vẹn vào trải nghiệm thị giác của nhân vật hòa cùng hình nền và nhịp điệu âm nhạc sống động.

### 1.2. Mục tiêu tính năng
Xây dựng chức năng tạo video tự động chuẩn dọc 9:16 (1080×1920) hoặc ngang 16:9 (1920×1080) với các đầu vào tinh gọn:
1. **01 Ảnh Background (Hình nền):** Phong cảnh, phòng thu Lofi, sân khấu, không gian vũ trụ/neon, hoặc gradient trừu tượng.
2. **01 Ảnh Nhân vật (Character / Ca sĩ / Model):** Ảnh chân dung/toàn thân nhân vật (ảnh PNG tách nền trong suốt hoặc ảnh được lồng trong đĩa than xoay / khung kính nghệ thuật) **nằm bên trong background**.
3. **01 File Âm thanh (Nhạc MP3 / WAV):** Bài hát, bản phối remix, lofi beat, ballad lấy từ `audio.library` hoặc tải lên trực tiếp.
4. **Bộ Hiệu ứng Âm nhạc Tự động (Pure Music Effects Suite):** Sóng nhạc thời gian thực (Audio Visualizer), Đĩa than xoay (Spinning Vinyl), Hạt ánh sáng lơ lửng (Bokeh/Dust/Rain), Nhún nảy theo nhịp Bass (Beat Bounce/Pulse), và Chớp sáng theo nhịp điệu (Beat Flash).

> **Lưu ý thiết kế:** Không chèn text tên bài hát, ca sĩ hay phụ đề lời bài hát, giữ khung hình sạch sẽ, thẩm mỹ cao và tập trung 100% vào nhân vật và hiệu ứng âm nhạc.

---

## 2. Kiến trúc Xử lý & Phân tầng Visual Layers

Toàn bộ video được render trong **duy nhất 1-Pass FFmpeg Filter Complex** với các lớp đồ họa được xếp chồng chuẩn xác:

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        CÁC LỚP HÌNH ẢNH & HIỆU ỨNG                     │
├────────────────────────────────────────────────────────────────────────┤
│ Layer 3: HIỆU ỨNG KHÔNG KHÍ & ÁNH SÁNG (Bokeh Particles, Rain, Flash)  │
├────────────────────────────────────────────────────────────────────────┤
│ Layer 2: SÓNG NHẠC VISUALIZER (Spectrum Bars / Radial Wave / Sine Wave)│
├────────────────────────────────────────────────────────────────────────┤
│ Layer 1: ẢNH NHÂN VẬT (Character: Cutout / Vinyl / Glass Card + Pulse) │
├────────────────────────────────────────────────────────────────────────┤
│ Layer 0: ẢNH BACKGROUND (Cover 1080x1920 + Slow Zoom/Pan + Vignette)   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Các Chế độ Bố cục Nhân vật (Character Layout Modes)

Hệ thống cung cấp 4 kiểu bố cục (Layouts) nghệ thuật để người dùng lựa chọn:

```text
  [ Layout A: Center Cutout ]       [ Layout B: Spinning Vinyl ]     [ Layout C: Glass Card ]
┌─────────────────────────────┐   ┌─────────────────────────────┐   ┌─────────────────────────────┐
│                             │   │                             │   │                             │
│       *     ✦       *       │   │       ✦     *       ✦       │   │       *     ✦       *       │
│                             │   │                             │   │                             │
│            ( ◠‿◠ )          │   │           ╭─────╮           │   │      ┌───────────────┐      │
│           /|     |\         │   │         /  (•‿•)  \         │   │      │   Character   │      │
│          / |     | \        │   │        |  Avatar   |        │   │      │     Photo     │      │
│         /  |     |  \       │   │         \  Center /         │   │      └───────────────┘      │
│       (Nhân vật tách nền)   │   │           ╰─────╯           │   │     (Khung kính mờ neon)    │
│      ||||||||||||||||||     │   │      (Đĩa than xoay 360°)   │   │                             │
│         (Sóng nhạc)         │   │      ░░░░░░░░░░░░░░░░░      │   │      ||||||||||||||||       │
│       *             *       │   │       *             *       │   │       *             *       │
│                             │   │                             │   │                             │
└─────────────────────────────┘   └─────────────────────────────┘   └─────────────────────────────┘
```

### 3.1. Layout A: Nhân vật Tách nền (Center Artist Cutout)
- **Đặc điểm:** Ảnh nhân vật đã tách nền (PNG trong suốt). Nhân vật đứng ở giữa hoặc 1/3 dưới màn hình, hòa trộn tự nhiên vào background.
- **Hiệu ứng đi kèm:**
  - Viền phát sáng nhẹ (Outer Glow / Neon Shadow) bao quanh nhân vật.
  - Chuyển động "nhịp thở" (Breathing motion) và **Beat Bounce** (nhún nảy nhẹ theo tiếng trống bass/kick).
  - Sóng nhạc Equalizer nằm ngang ngay dưới chân hoặc phía sau nhân vật.

### 3.2. Layout B: Đĩa than Xoay (Spinning Vinyl Record)
- **Đặc điểm:** Ảnh nhân vật được cắt tròn làm nhãn tâm đĩa than (Vinyl Label). Toàn bộ đĩa than xoay tròn liên tục `360 độ` theo thời gian thực.
- **Hiệu ứng đi kèm:**
  - Sóng nhạc tròn (Radial / Circular Audio Visualizer) tỏa hào quang bao quanh viền đĩa than.
  - Hiệu ứng đĩa rung lắc nhẹ khi gặp tiếng Bass Drop / Kick Beat.
  - Phù hợp tuyệt đối với nhạc Lofi, R&B, Chill, Ballad.

### 3.3. Layout C: Khung kính Mờ Nghệ thuật (Glassmorphism Card)
- **Đặc điểm:** Ảnh nhân vật được đặt trong một Card hình chữ nhật bo góc với hiệu ứng viền LED sáng và đổ bóng kính mờ.
- **Hiệu ứng đi kèm:**
  - Thẻ card zoom in từ từ kèm hiệu ứng đổ bóng mờ trên background.
  - Phù hợp với ảnh gốc chưa tách nền (ảnh vuông 1:1 hoặc ảnh chữ nhật).

### 3.4. Layout D: Avatar Tròn Hào quang (Circular Avatar & Halo Wave)
- **Đặc điểm:** Ảnh nhân vật được crop tròn ở trung tâm, bao bọc bởi 2-3 lớp sóng nhạc đồng tâm tỏa ra xung quanh.
- **Hiệu ứng đi kèm:** Sóng âm thanh nhảy linh hoạt theo từng dải tần số thấp (Bass) và cao (Treble).

---

## 4. Danh mục Hiệu ứng Ca nhạc Chi tiết (Music Effects Suite)

### 4.1. Hiệu ứng Sóng nhạc Realtime (Audio Visualizer)
Tạo trực tiếp từ luồng âm thanh thông qua FFmpeg:
1. **Spectrum Bars (Cột sóng Equalizer):** Các cột sóng nhảy theo biên độ và tần số âm thanh (`showfreqs` / `showwaves`).
2. **Smooth Sine Waveform (Dải sóng lượn sóng mượt):** Đường sóng âm thanh mềm mại phát sáng neon (`showwaves=mode=line:colors=cyan`).
3. **Radial / Circular Visualizer (Vòng sóng âm thanh tròn):** Tỏa sáng xung quanh nhân vật hoặc đĩa than.

### 4.2. Hiệu ứng Đồng bộ Beat (Beat Pulse & Bass Shake)
- **Phân tích nhịp:** Sử dụng thuật toán Onset RMS Energy Detection trên dải tần Bass (20Hz – 250Hz) để bắt chính xác các nhịp Kick/Drop.
- **Beat Bounce:** Zoom kích thước nhân vật tăng `+4% đến +8%` trong `0.1s` rồi đàn hồi lại ngay tại mỗi beat.
- **Lighting Flash:** Chớp sáng nhẹ (`White Flash` độ trong suốt `0.08 - 0.15`) tại điểm nhấn nhịp mạnh.

### 4.3. Hiệu ứng Hạt & Môi trường Âm nhạc (Atmospheric Particles)
- **Dust & Bokeh Particles:** Các đốm sáng và hạt bụi vàng/trắng lơ lửng chầm chậm, tạo cảm giác mộng mơ (Lofi/Ballad).
- **Raindrops / Fog Effect:** Giọt mưa rơi trên nền kính mờ hoặc lớp sương mù chuyển động.
- **Stage Light Beams:** Các tia đèn sân khấu đổi hướng quét nhẹ qua khung hình.
- **Neon Glitch & RGB Split:** Hiệu ứng tách màu RGB và rung nhẹ khi chuyển sang đoạn điệp khúc (EDM / Remix).

---

## 5. Cấu hình Preset Thể loại Nhạc (Music Presets)

| Preset | Thể loại phù hợp | Hiệu ứng Background | Hiệu ứng Nhân vật | Visualizer & Hạt | Cường độ Beat |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`lofi_chill`** | Lofi, R&B, Nhạc thư giãn | Blur ấm + Slow Zoom | Đĩa than xoay / Khung kính | Sóng mềm + Hạt bụi Bokeh | Rất nhẹ (Soft) |
| **`edm_remix`** | EDM, Vinahouse, Remix | Đổi màu + Flash nền | Tách nền + Bass Bounce mạnh | Cột Spectrum Neon + Laser | Cực mạnh (Strong) |
| **`ballad_acoustic`**| Ballad, Acoustic, Guitar | Mờ ảo (Dreamy Blur) | Chân dung tĩnh + Nhịp thở | Sóng Sine phát sáng + Giọt mưa | Nhẹ nhàng (Soft) |
| **`hiphop_cyber`** | Rap, HipHop, Cyberpunk | Tối viền + Neon City | Viền Neon Glow sáng | Sóng Equalizer kép + RGB Glitch| Tiêu chuẩn (Normal) |

---

## 6. Thiết kế Data Schema (Odoo Models)

### 6.1. Mở rộng Model: `video.library`

Thêm các trường mới phục vụ tạo video ca nhạc (không cần thông tin bài hát và lời):

```python
class VideoLibrary(models.Model):
    _inherit = "video.library"

    video_type = fields.Selection(
        [
            ("raw_video", "Video thô (Raw Video)"),
            ("affiliate_product", "Ảnh sản phẩm Affiliate"),
            ("music_video", "Video Ca Nhạc (Music Video)"),
        ],
        default="raw_video",
        string="Loại Video",
        required=True,
        tracking=True,
    )

    # Assets đầu vào cho Video Ca Nhạc
    bg_image_id = fields.Many2one(
        "product.image",
        string="Ảnh Background",
        domain=[("state", "=", "uploaded"), ("image_type", "=", "background")],
        help="Ảnh nền cho video ca nhạc",
    )
    character_image_id = fields.Many2one(
        "product.image",
        string="Ảnh Nhân vật / Ca sĩ",
        domain=[("state", "=", "uploaded"), ("image_type", "=", "character")],
        help="Ảnh nhân vật (khuyên dùng ảnh PNG tách nền)",
    )
    
    # Cấu hình Bố cục & Hiệu ứng
    music_layout = fields.Selection(
        [
            ("center_cutout", "Nhân vật Tách nền (Center Cutout)"),
            ("spinning_vinyl", "Đĩa than xoay (Spinning Vinyl)"),
            ("glass_card", "Khung kính mờ (Glassmorphism Card)"),
            ("circular_avatar", "Avatar tròn sóng nhạc (Circular Avatar)"),
        ],
        default="center_cutout",
        string="Kiểu bố cục nhân vật",
    )
    
    visualizer_style = fields.Selection(
        [
            ("none", "Không hiển thị"),
            ("spectrum_bars", "Cột sóng Equalizer (Spectrum Bars)"),
            ("sine_wave", "Đường sóng lượn (Smooth Wave)"),
            ("radial_circle", "Sóng tròn bao quanh (Radial Wave)"),
        ],
        default="spectrum_bars",
        string="Kiểu sóng nhạc (Visualizer)",
    )
    
    visualizer_color = fields.Selection(
        [
            ("cyan_neon", "Xanh Neon (Cyan Glow)"),
            ("pink_purple", "Hồng Tím Neon (Synthwave Pink)"),
            ("golden_warm", "Vàng Ánh Kim (Golden Glow)"),
            ("white_minimal", "Trắng Tối giản (Pure White)"),
        ],
        default="cyan_neon",
        string="Màu sóng nhạc",
    )
    
    particle_effect = fields.Selection(
        [
            ("none", "Không có"),
            ("dust_bokeh", "Hạt bụi sáng (Dust & Bokeh)"),
            ("rain_drops", "Giọt mưa rơi (Rain Drops)"),
            ("stage_lights", "Tia đèn sân khấu (Stage Lights)"),
        ],
        default="dust_bokeh",
        string="Hiệu ứng không khí (Particles)",
    )

    music_preset = fields.Selection(
        [
            ("lofi_chill", "Lofi / Chill (Nhẹ nhàng, Đĩa than xoay)"),
            ("edm_remix", "EDM / Remix / Vinahouse (Bass Bounce, Flash mạnh)"),
            ("ballad_acoustic", "Ballad / Acoustic (Mộng ảo, Lắng đọng)"),
            ("hiphop_cyber", "Rap / HipHop / Cyberpunk (Neon, Cá tính)"),
        ],
        default="lofi_chill",
        string="Preset Thể loại nhạc",
    )
```

---

## 7. Thiết kế Kỹ thuật FFmpeg (Core Pipeline)

### 7.1. Kiến trúc Pipeline 1-Pass trong `services/ffmpeg_service.py`

Hàm `generate_music_video(...)` nhận vào các đường dẫn file và xây dựng Filter Complex tinh gọn:

```python
def generate_music_video(
    bg_image_path: str,
    character_image_path: str,
    audio_path: str,
    output_path: str,
    layout: str = "center_cutout",
    visualizer_style: str = "spectrum_bars",
    visualizer_color: str = "cyan_neon",
    particle_effect: str = "dust_bokeh",
    music_preset: str = "lofi_chill",
    beats: list = None,
    audio_duration: float = 30.0,
):
    """
    Tạo video ca nhạc 1080x1920 30FPS chuẩn TikTok/Shorts từ Background + Character + Audio.
    Tất cả ghép trong 1 lần encode FFmpeg tối ưu, không text hay subtitle rườm rà.
    """
    ...
```

### 7.2. Chi tiết Filter Complex cho từng thành phần

#### A. Chuẩn hóa Background (Layer 0)
Background được phủ kín tỷ lệ 9:16 (1080x1920) kèm hiệu ứng phóng to nhẹ (Ken Burns) và tối 4 góc (Vignette) để làm bật nhân vật:
```text
[0:v]scale=1080:1920:force_original_aspect_ratio=increase,
     crop=1080:1920,
     zoompan=z='min(zoom+0.0005,1.15)':d=1:s=1080x1920:fps=30,
     vignette=PI/4[bg]
```

#### B. Xử lý Đĩa than xoay (Layout `spinning_vinyl`)
Cắt tròn ảnh nhân vật làm tâm đĩa và quay theo thời gian `t`:
```text
[1:v]scale=560:560,format=rgba,
     geq=r='r(X,Y)':a='if(lte((X-280)*(X-280)+(Y-280)*(Y-280),280*280),255,0)'[avatar_circle];
[avatar_circle]rotate=2*PI*t*0.25:c=none:ow='hypot(iw,ih)':oh=ow[vinyl_spinning];
[bg][vinyl_spinning]overlay=(W-w)/2:(H-h)/2[bg_with_vinyl]
```

#### C. Xử lý Nhân vật Tách nền & Beat Bounce (Layout `center_cutout`)
Nhân vật được co giãn kích thước theo hàm xung nhịp tại các thời điểm có `beat`:
```text
# scale_expr = 1.0 + amplitude * (1 - (t - beat_time)/duration)
[1:v]scale=w='trunc(iw*(1.0+bounce_term)/2)*2':h='trunc(ih*(1.0+bounce_term)/2)*2':eval=frame[char_pulsed];
[bg][char_pulsed]overlay=(W-w)/2:H-h-180[bg_with_char]
```

#### D. Tạo Sóng nhạc Realtime (Layer 2 - Audio Visualizer)
Sinh sóng nhạc trực tiếp từ audio stream và phủ lên background:
```text
# Spectrum Bars nhảy theo dải tần âm thanh
[2:a]showfreqs=s=900x200:mode=bar:ascale=log:fscale=log:colors=0x00ffff|0xff00ff[vis_raw];
[vis_raw]format=rgba,colorchannelmixer=aa=0.85[vis_transparent];
[bg_with_char][vis_transparent]overlay=(W-w)/2:1400[stage_with_vis]
```

#### E. Hiệu ứng Chớp sáng Beat & Particles (Layer 3)
Chớp sáng nhẹ (Flash) hoặc nháy tương phản/độ sáng khi beat xuất hiện:
```text
[stage_with_vis]drawbox=x=0:y=0:w=iw:h=ih:color=white@0.12:t=fill:enable='between(t,beat_start,beat_end)'[vout]
```

---

## 8. Thiết kế Giao diện Odoo (UI / UX)

### 8.1. Form View Tạo Video Ca Nhạc (`video.library`)
- Tab **"Cấu hình Video Ca Nhạc"**:
  - Nhóm 1: **Chọn Assets:** Trường Many2one chọn Ảnh Background, Ảnh Nhân vật, và File Nhạc. Có nút `Upload nhanh` cho từng ảnh.
  - Nhóm 2: **Phong cách & Bố cục:** Chọn Layout (Đĩa than, Nhân vật tách nền, Khung kính), Chọn Preset (Lofi, EDM, Ballad, Hiphop).
  - Nhóm 3: **Sóng nhạc & Hiệu ứng:** Chọn kiểu sóng, màu sắc và hiệu ứng hạt.
- Header Action Button:
  - **`[ 🎬 Tạo Video Ca Nhạc ]`**: Kích hoạt worker render video chạy nền (asynchronous qua Job Queue).
  - **`[ 👁 Xem trước Bố cục (Preview Frame) ]`**: Tạo ngay 1 frame ảnh tại giây thứ 5 để người dùng duyệt trước khi render toàn bộ video.

### 8.2. Wizard Tạo Video Hàng Loạt (Bulk Music Video Wizard)
Cho phép người dùng chọn danh sách 20 ảnh nhân vật + 20 ảnh nền + 1 thư viện nhạc -> Tự động phối ghép ngẫu nhiên (hoặc theo cặp) và đẩy vào hàng đợi tạo 20 video ca nhạc tự động chỉ với 1 cú click chuột.

---

## 9. Kế hoạch Triển khai (Roadmap)

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        KẾ HOẠCH TRIỂN KHAI                             │
├────────────────────────────────────────────────────────────────────────┤
│ Giai đoạn 1: Database & Model (Mở rộng video.library, product.image)  │
│             - Thêm các trường video_type, music_layout, visualizer     │
├────────────────────────────────────────────────────────────────────────┤
│ Giai đoạn 2: FFmpeg Service Enhancement                                │
│             - Viết hàm generate_music_video() trong ffmpeg_service.py │
│             - Tích hợp Audio Visualizer, Vinyl Rotate, Beat Bounce     │
├────────────────────────────────────────────────────────────────────────┤
│ Giai đoạn 3: Odoo Views & Action Wizard                                │
│             - Thiết kế giao diện Form View, Tree View, Wizard          │
├────────────────────────────────────────────────────────────────────────┤
│ Giai đoạn 4: Test Render & Tối ưu hóa hiệu năng                       │
│             - Kiểm thử tốc độ render trên CPU/GPU, đồng bộ nhịp nhạc   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 10. Kết luận & Đề xuất Bước tiếp theo

Chức năng **Tạo Video Ca Nhạc (`video_type = 'music_video'`)** tập trung hoàn toàn vào phong cách thị giác nghệ thuật cao (Background + Nhân vật + Sóng nhạc + Hiệu ứng Beat) mà không cần text rườm rà.

👉 **Đề xuất thực hiện tiếp theo:**
1. Cập nhật model [video_library.py](file:///Users/macos/project/personal/odoo/odoo/project/video_automation/addons/video_automation/models/video_library.py).
2. Xây dựng logic render FFmpeg âm nhạc trong [ffmpeg_service.py](file:///Users/macos/project/personal/odoo/odoo/project/video_automation/addons/video_automation/services/ffmpeg_service.py).
3. Bổ sung giao diện Form View trong [video_library_views.xml](file:///Users/macos/project/personal/odoo/odoo/project/video_automation/addons/video_automation/views/video_library_views.xml).
