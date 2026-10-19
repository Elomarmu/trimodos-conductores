import os
from dataclasses import dataclass

from dotenv import load_dotenv

# ENV_FILE permite apuntar a otro Odoo sin tocar el .env de producción (p. ej. ENV_FILE=.env.odoo18)
load_dotenv(os.environ.get("ENV_FILE") or None)


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "si", "sí")


@dataclass(frozen=True)
class Settings:
    mode: str
    dry_run: bool
    odoo_url: str
    odoo_db: str
    odoo_user: str
    odoo_password: str
    odoo_web_password: str   # solo para imprimir el PDF (sesión web); si está vacía se usa odoo_password
    company_ids: list[int]
    secret_key: str
    qr_allowed_hosts: list[str]
    access_code: str
    editable_stages: list[str]   # etapas de la tarea en las que se puede modificar


def get_settings() -> Settings:
    """Lee el entorno cada vez (barato) para que los tests puedan cambiarlo."""
    mode = os.environ.get("ODOO_MODE", "mock").strip().lower()
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        if mode == "real":
            raise RuntimeError("Falta SECRET_KEY en el entorno (.env).")
        secret = "clave-solo-para-desarrollo"
    return Settings(
        mode=mode,
        dry_run=_bool(os.environ.get("DRY_RUN"), True),
        odoo_url=os.environ.get("ODOO_URL", "").rstrip("/"),
        odoo_db=os.environ.get("ODOO_DB", ""),
        odoo_user=os.environ.get("ODOO_USER", ""),
        odoo_password=os.environ.get("ODOO_PASSWORD", ""),
        odoo_web_password=os.environ.get("ODOO_WEB_PASSWORD", "") or os.environ.get("ODOO_PASSWORD", ""),
        company_ids=[int(x) for x in os.environ.get("ODOO_COMPANY_IDS", "1,7").split(",") if x.strip()],
        secret_key=secret,
        qr_allowed_hosts=[h.strip().lower() for h in os.environ.get("QR_ALLOWED_HOSTS", "").split(",") if h.strip()],
        access_code=os.environ.get("APP_ACCESS_CODE", "").strip(),
        editable_stages=[x.strip() for x in os.environ.get("EDITABLE_STAGES", "nuevo,en desarrollo").split(",") if x.strip()],
    )
