FROM odoo:17.0

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && pip3 install --no-cache-dir \
        boto3==1.35.99 \
        requests==2.32.3 \
    && mkdir -p /tmp/video_work \
    && chown -R odoo:odoo /tmp/video_work \
    && rm -rf /var/lib/apt/lists/*

USER odoo
