"""GET/POST /client-attendance — kehadiran per peserta (paket semi privat).

Dipakai hitung komisi semi privat: 1 peserta hadir = 75.000, 2 hadir = 85.000
per sesi. Pola sama seperti /attendance (upsert berdasarkan id)."""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db

from .. import models
from ..access import get_current_user

router = APIRouter()


class ClientAttendanceIn(BaseModel):
    date: str
    coach_id: str
    time: str
    client_id: str
    status: str
    by_name: Optional[str] = None
    at: Optional[str] = None


@router.get("/client-attendance")
def list_client_attendance(date: Optional[str] = None,
                           coach_id: Optional[str] = None,
                           client_id: Optional[str] = None,
                           user: models.Member = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    q = db.query(models.ClientAttendance)
    if date:
        q = q.filter_by(date=date)
    if coach_id:
        q = q.filter_by(coach_id=coach_id)
    if client_id:
        q = q.filter_by(client_id=client_id)
    rows = q.all()
    return {"data": [
        {"id": r.id, "date": r.date, "coach_id": r.coach_id, "time": r.time,
         "client_id": r.client_id, "status": r.status, "by_name": r.by_name,
         "at": r.at}
        for r in rows
    ]}


@router.post("/client-attendance", status_code=201)
def save_client_attendance(body: ClientAttendanceIn,
                           user: models.Member = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    rid = f"{body.date}|{body.coach_id}|{body.time}|{body.client_id}"
    r = db.query(models.ClientAttendance).filter_by(id=rid).first()
    if r:
        r.status = body.status
        r.by_name = body.by_name
        r.at = body.at
    else:
        r = models.ClientAttendance(
            id=rid, date=body.date, coach_id=body.coach_id, time=body.time,
            client_id=body.client_id, status=body.status,
            by_name=body.by_name, at=body.at)
        db.add(r)
    db.commit()
    return {"data": {"id": rid}}
