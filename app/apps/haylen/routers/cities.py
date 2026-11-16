"""Kota (data master) + tarif program per kota.

- GET/POST /cities, PATCH /cities/{id}, DELETE /cities/{id}
- GET /program-rates, GET /program-rates/{pid}, PUT /program-rates,
  DELETE /program-rates/{pid}/{cid}

Aturan: nama kota unik; hapus kota diblokir bila masih dipakai klien,
anggota, atau tarif. Timpaan tarif: price/fee NULL = pakai tarif bawaan
program; PUT butuh minimal satu dari price/fee terisi.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db

from .. import models, schemas
from ..access import get_current_user
from ..ids import next_id

router = APIRouter()


def _out_city(c):
    return {"id": c.id, "name": c.name}


def _out_rate(r):
    return {"program_id": r.program_id, "city_id": r.city_id,
            "price": r.price, "fee": r.fee,
            "semi_fee_1": r.semi_fee_1, "semi_fee_2": r.semi_fee_2}


def _get_city(db, cid):
    c = db.query(models.City).filter_by(id=cid).first()
    if not c:
        raise HTTPException(status_code=404, detail="Kota tidak ada")
    return c


def _get_program(db, pid):
    p = db.query(models.Program).filter_by(id=pid).first()
    if not p:
        raise HTTPException(status_code=404, detail="Program tidak ada")
    return p


# ---------- kota ----------
@router.get("/cities")
def list_cities(user: models.Member = Depends(get_current_user),
                db: Session = Depends(get_db)):
    return {"data": [_out_city(c) for c in
                     db.query(models.City).order_by(models.City.name).all()]}


@router.post("/cities", status_code=201)
def create_city(body: schemas.CityIn,
                user: models.Member = Depends(get_current_user),
                db: Session = Depends(get_db)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nama kota wajib diisi")
    if db.query(models.City).filter_by(name=name).first():
        raise HTTPException(status_code=400, detail="Kota sudah ada")
    c = models.City(id=next_id(db, models.City, "ct"), name=name)
    db.add(c); db.commit(); db.refresh(c)
    return {"data": _out_city(c)}


@router.patch("/cities/{cid}")
def patch_city(cid: str, body: schemas.CityPatch,
               user: models.Member = Depends(get_current_user),
               db: Session = Depends(get_db)):
    c = _get_city(db, cid)
    name = (body.name or "").strip() if body.name is not None else None
    if name is not None:
        if not name:
            raise HTTPException(status_code=400, detail="Nama kota wajib diisi")
        other = db.query(models.City).filter_by(name=name).first()
        if other and other.id != cid:
            raise HTTPException(status_code=400, detail="Kota sudah ada")
        c.name = name
    db.commit(); db.refresh(c)
    return {"data": _out_city(c)}


@router.delete("/cities/{cid}", status_code=204)
def delete_city(cid: str, user: models.Member = Depends(get_current_user),
                db: Session = Depends(get_db)):
    c = _get_city(db, cid)
    blockers = []
    n = db.query(models.Client).filter_by(city_id=cid).count()
    if n:
        blockers.append(f"{n} klien")
    n = db.query(models.Member).filter_by(city_id=cid).count()
    if n:
        blockers.append(f"{n} anggota")
    n = db.query(models.ProgramCityRate).filter_by(city_id=cid).count()
    if n:
        blockers.append(f"{n} tarif")
    if blockers:
        raise HTTPException(status_code=400,
                            detail="Kota masih dipakai: " + ", ".join(blockers) + ".")
    db.delete(c)
    db.commit()
    return None


# ---------- tarif program per kota ----------
@router.get("/program-rates")
def list_rates(user: models.Member = Depends(get_current_user),
               db: Session = Depends(get_db)):
    return {"data": [_out_rate(r) for r in
                     db.query(models.ProgramCityRate).all()]}


@router.get("/program-rates/{pid}")
def list_rates_for_program(pid: str,
                           user: models.Member = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    _get_program(db, pid)
    return {"data": [_out_rate(r) for r in
                     db.query(models.ProgramCityRate)
                     .filter_by(program_id=pid).all()]}


@router.put("/program-rates")
def upsert_rate(body: schemas.ProgramRateIn,
                user: models.Member = Depends(get_current_user),
                db: Session = Depends(get_db)):
    _get_program(db, body.program_id)
    _get_city(db, body.city_id)
    if (body.price is None and body.fee is None
            and body.semi_fee_1 is None and body.semi_fee_2 is None):
        raise HTTPException(status_code=400,
                            detail="Isi harga atau komisi (atau hapus timpaan).")
    for v in (body.price, body.fee, body.semi_fee_1, body.semi_fee_2):
        if v is not None and v < 0:
            raise HTTPException(status_code=400,
                                detail="Harga dan komisi tidak boleh negatif")
    r = (db.query(models.ProgramCityRate)
         .filter_by(program_id=body.program_id, city_id=body.city_id).first())
    if not r:
        r = models.ProgramCityRate(program_id=body.program_id,
                                   city_id=body.city_id)
        db.add(r)
    r.price = body.price
    r.fee = body.fee
    r.semi_fee_1 = body.semi_fee_1
    r.semi_fee_2 = body.semi_fee_2
    db.commit(); db.refresh(r)
    return {"data": _out_rate(r)}


@router.delete("/program-rates/{pid}/{cid}", status_code=204)
def delete_rate(pid: str, cid: str,
                user: models.Member = Depends(get_current_user),
                db: Session = Depends(get_db)):
    r = (db.query(models.ProgramCityRate)
         .filter_by(program_id=pid, city_id=cid).first())
    if not r:
        raise HTTPException(status_code=404, detail="Timpaan tarif tidak ada")
    db.delete(r)
    db.commit()
    return None
