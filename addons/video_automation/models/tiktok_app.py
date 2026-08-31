from odoo import api, fields, models


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

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            for field_name in ("client_key", "client_secret", "redirect_uri"):
                if vals.get(field_name) and isinstance(vals[field_name], str):
                    vals[field_name] = vals[field_name].strip()
        return super().create(vals_list)

    def write(self, vals):
        for field_name in ("client_key", "client_secret", "redirect_uri"):
            if vals.get(field_name) and isinstance(vals[field_name], str):
                vals[field_name] = vals[field_name].strip()
        return super().write(vals)
