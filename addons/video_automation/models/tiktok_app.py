from odoo import fields, models


class TikTokApp(models.Model):
    _name = "tiktok.app"
    _description = "TikTok Developer App"
    _order = "name"

    name = fields.Char(required=True)
    client_key = fields.Char(required=True)
    client_secret = fields.Char(required=True)
    redirect_uri = fields.Char(
        required=True,
        default="http://localhost:8069/tiktok/oauth/callback",
    )
    environment = fields.Selection(
        [
            ("sandbox", "Sandbox"),
            ("production", "Production"),
        ],
        default="sandbox",
        required=True,
    )
    active = fields.Boolean(default=True)
    account_ids = fields.One2many("tiktok.account", "tiktok_app_id", string="Accounts")
