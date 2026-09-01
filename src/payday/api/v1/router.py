from fastapi import APIRouter
from payday.api.v1.auth import router as auth_router
from payday.api.v1.kyc import router as kyc_router
from payday.api.v1.wallet import router as wallet_router
from payday.api.v1.admin import router as admin_router
from payday.api.v1.public import router as public_router

api_router = APIRouter()
api_router.include_router(public_router)
api_router.include_router(auth_router)
api_router.include_router(kyc_router)
api_router.include_router(wallet_router)
api_router.include_router(admin_router)
