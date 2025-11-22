import os
from math import radians, sin, cos, asin, sqrt
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId

from database import db, create_document, get_documents

app = FastAPI(title="Store Finder API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StoreCreate(BaseModel):
    name: str
    address: str
    city: str
    country: str
    location: dict  # { type: "Point", coordinates: [lng, lat] }
    inventory: list = []


class ProductSearchResponse(BaseModel):
    store_id: str
    store_name: str
    address: str
    distance_km: float
    product_title: str
    price: float
    quantity: int


def haversine(lon1, lat1, lon2, lat2):
    """Calculate the great circle distance between two points on the earth (specified in decimal degrees)"""
    # convert decimal degrees to radians
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

    # haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    km = 6367 * c
    return km


@app.get("/")
def read_root():
    return {"message": "Store Finder API running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    import os
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


@app.post("/api/stores")
def create_store(store: StoreCreate):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    # Basic validation for GeoJSON point
    loc = store.location
    if not (isinstance(loc, dict) and loc.get("type") == "Point" and isinstance(loc.get("coordinates"), list) and len(loc["coordinates"]) == 2):
        raise HTTPException(status_code=400, detail="Invalid location. Expect GeoJSON Point with [lng, lat]")

    store_id = create_document("store", store)
    return {"id": store_id}


@app.get("/api/stores")
def list_stores():
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    stores = get_documents("store")
    # Convert ObjectId
    for s in stores:
        s["id"] = str(s.get("_id"))
        s.pop("_id", None)
    return stores


@app.get("/api/search", response_model=List[ProductSearchResponse])
def search_product(
    q: str = Query(..., description="Product title to search for"),
    lat: float = Query(..., description="User latitude"),
    lng: float = Query(..., description="User longitude"),
    radius_km: float = Query(50, description="Search radius in kilometers"),
):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    # Find stores that have the product in inventory
    # Using a simple case-insensitive match on inventory.product_title
    cursor = db["store"].find({
        "inventory": {
            "$elemMatch": {
                "product_title": {"$regex": q, "$options": "i"},
                "quantity": {"$gt": 0}
            }
        }
    })

    results: List[ProductSearchResponse] = []
    for s in cursor:
        coords = s.get("location", {}).get("coordinates", [None, None])
        if coords is None or len(coords) != 2:
            continue
        store_lng, store_lat = coords
        distance = haversine(lng, lat, store_lng, store_lat)
        if distance <= radius_km:
            # Find best matching inventory item
            matches = [item for item in s.get("inventory", []) if q.lower() in str(item.get("product_title", "")).lower() and item.get("quantity", 0) > 0]
            if not matches:
                continue
            # Choose the cheapest option
            best = sorted(matches, key=lambda x: x.get("price", 0))[0]
            results.append(ProductSearchResponse(
                store_id=str(s.get("_id")),
                store_name=s.get("name"),
                address=s.get("address"),
                distance_km=round(distance, 2),
                product_title=best.get("product_title"),
                price=float(best.get("price", 0)),
                quantity=int(best.get("quantity", 0)),
            ))

    # Sort by distance then price
    results.sort(key=lambda r: (r.distance_km, r.price))
    return results


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
