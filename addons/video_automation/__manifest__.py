{
    "name": "Video Automation",
    "version": "17.0.3.0.0",
    "category": "Marketing",
    "summary": "TikTok Affiliate Video Generator + Cloudflare R2 + Auto Schedule",
    "description": """
Video Automation Auto Schedule & TikTok Affiliate Video Generator
=================================================================
- TikTok Affiliate Video Generator (1 Product Image + 1 MP3 to 9:16 Video)
- Auto Gen Queue (FIFO, generated=False)
- Onset Energy Beat Detection & Beat Pulse + White Flash Effect
- Safe Area Hook & CTA Text Overlay
- Bulk Image Upload & REST API Endpoints
- Cloudflare R2 Storage Integration
- Auto top-up pool from Raw Videos & Pending Product Images
- Schedule & publish queue to TikTok Inbox
    """,
    "author": "Video Automation",
    "license": "LGPL-3",
    "depends": ["base", "mail"],
    "external_dependencies": {
        "python": ["boto3", "requests", "numpy"],
        "bin": ["ffmpeg", "ffprobe"],
    },
    "data": [
        "security/video_automation_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "data/ir_cron_topup.xml",
        "views/video_storage_views.xml",
        "views/image_storage_views.xml",
        "views/product_image_views.xml",
        "views/tiktok_app_views.xml",
        "views/video_library_views.xml",
        "views/audio_library_views.xml",
        "views/video_generate_job_views.xml",
        "views/tiktok_account_views.xml",
        "views/tiktok_schedule_rule_views.xml",
        "views/tiktok_publish_queue_views.xml",
        "views/tiktok_upload_history_views.xml",
        "wizard/video_extract_audio_wizard_views.xml",
        "wizard/video_generate_wizard_views.xml",
        "wizard/tiktok_publish_wizard_views.xml",
        "wizard/product_image_bulk_wizard_views.xml",
        "wizard/audio_library_bulk_wizard_views.xml",
        "views/menu_views.xml",
    ],

    "installable": True,
    "application": True,
}

