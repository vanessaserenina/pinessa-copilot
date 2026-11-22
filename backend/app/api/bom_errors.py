from fastapi import HTTPException

from app.modules.products.bom_management import BomRuleViolation

_STATUS = {"not_found": 404, "conflict": 409, "invalid": 422}


def to_http(exc: BomRuleViolation) -> HTTPException:
    """Rules are checked before anything is changed, so there is nothing to roll back here."""
    return HTTPException(status_code=_STATUS[exc.kind], detail=exc.message)