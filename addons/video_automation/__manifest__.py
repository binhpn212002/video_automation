{
    "name": "Video Automation",
    "version": "17.0.2.8.0",
    "category": "Marketing",
    "summary": "Cloudflare R2 + Auto Schedule + TikTok Inbox Draft",
    "description": """
Video Automation Auto Schedule
==============================
- Store media on Cloudflare R2
- Generate videos with FFmpeg
- Auto-create TikTok publish queue (FIFO)
- Upload draft to TikTok Inbox (FILE_UPLOAD via temp → user Edit → Post)
    """,
    "author": "Video Automation",
    "license": "LGPL-3",
    "depends": ["base", "mail"],
    "external_dependencies": {
        "python": ["boto3", "requests"],
        "bin": ["ffmpeg", "ffprobe"],
    },
    "data": [
        "security/video_automation_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "views/video_storage_views.xml",
        "views/tiktok_app_views.xml",
        "views/video_library_views.xml",
        "views/audio_library_views.xml",
        "views/tiktok_account_views.xml",
        "views/tiktok_schedule_rule_views.xml",
        "views/tiktok_publish_queue_views.xml",
        "views/tiktok_upload_history_views.xml",
        "wizard/video_extract_audio_wizard_views.xml",
        "wizard/video_generate_wizard_views.xml",
        "wizard/tiktok_publish_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
}
