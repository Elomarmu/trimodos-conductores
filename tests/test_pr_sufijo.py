from app.pr import pr_reglas as R


def test_referencia_sin_sufijo(monkeypatch):
    monkeypatch.delenv("PR_PEDIDO_SUFIJO", raising=False)
    assert R.referencia_pedido("2026-11-15") == "PR NOVIEMBRE"


def test_referencia_con_sufijo(monkeypatch):
    monkeypatch.setenv("PR_PEDIDO_SUFIJO", " APP")
    assert R.referencia_pedido("2026-11-15") == "PR NOVIEMBRE APP"
