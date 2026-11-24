import os
import json
import base64
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from google import genai
from google.genai import types
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.campaign import Campaign
from facebook_business.adobjects.adset import AdSet
from facebook_business.adobjects.adimage import AdImage
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.ad import Ad

app = FastAPI()

class GenerateRequest(BaseModel):
    gemini_key: str
    brand_name: str
    offer: str
    sub_detail: str

class LaunchAdRequest(BaseModel):
    meta_token: str
    ad_account_id: str
    page_id: str
    brand_name: str
    headline: str
    caption: str
    daily_budget_inr: int
    website_url: str
    poster_base64: str

class AdCopySchema(BaseModel):
    headline: str
    caption: str
    poster_bg_prompt: str
    logo_prompt: str

@app.get("/")
def serve_ui():
    return FileResponse("index.html")

@app.post("/api/generate")
def generate_assets(req: GenerateRequest):
    try:
        client = genai.Client(api_key=req.gemini_key)

        prompt = f"""
        Brand: {req.brand_name}
        Offer: {req.offer}
        Details: {req.sub_detail}
        Generate JSON with:
        1. headline: Short punchy ad headline (max 40 chars).
        2. caption: Engaging Facebook/Instagram ad caption with emojis & CTA.
        3. poster_bg_prompt: Rich commercial ad background image for '{req.brand_name}' related to '{req.offer}', clean center area for text overlay, no text inside image.
        4. logo_prompt: Minimalist modern vector brand icon logo on solid white background for '{req.brand_name}'.
        """
        plan_resp = client.models.generate_content(
            model="gemini-4-hydrogen",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=AdCopySchema,
            ),
        )
        plan = json.loads(plan_resp.text)

        bg_b64 = ""
        bg_resp = client.models.generate_content(
            model="gemini-3.1-flash-image",
            contents=[plan["poster_bg_prompt"]],
        )
        for part in bg_resp.parts:
            if part.inline_data is not None:
                part.as_image().save("temp_bg.png")
                with open("temp_bg.png", "rb") as f:
                    bg_b64 = base64.b64encode(f.read()).decode("utf-8")
                break

        logo_b64 = ""
        logo_resp = client.models.generate_content(
            model="gemini-3.1-flash-image",
            contents=[plan["logo_prompt"]],
        )
        for part in logo_resp.parts:
            if part.inline_data is not None:
                part.as_image().save("temp_logo.png")
                with open("temp_logo.png", "rb") as f:
                    logo_b64 = base64.b64encode(f.read()).decode("utf-8")
                break

        return {
            "headline": plan["headline"],
            "caption": plan["caption"],
            "bg_image": f"data:image/png;base64,{bg_b64}" if bg_b64 else "",
            "logo_image": f"data:image/png;base64,{logo_b64}" if logo_b64 else "",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/launch-ad")
def launch_meta_ad(req: LaunchAdRequest):
    try:
        clean_b64 = req.poster_base64.split(",")[-1]
        with open("final_ad_poster.png", "wb") as f:
            f.write(base64.b64decode(clean_b64))

        FacebookAdsApi.init(access_token=req.meta_token)
        acc_id = req.ad_account_id if req.ad_account_id.startswith("act_") else f"act_{req.ad_account_id}"

        campaign = Campaign(parent_id=acc_id)
        campaign.update({
            Campaign.Field.name: f"AdCanvas AI - {req.brand_name}",
            Campaign.Field.objective: "OUTCOME_TRAFFIC",
            Campaign.Field.status: Campaign.Status.paused,
            Campaign.Field.special_ad_categories: [],
        })
        campaign.remote_create()

        adset = AdSet(parent_id=acc_id)
        adset.update({
            AdSet.Field.name: f"{req.brand_name} - Audience",
            AdSet.Field.campaign_id: campaign.get_id(),
            AdSet.Field.daily_budget: int(req.daily_budget_inr * 100),
            AdSet.Field.billing_event: AdSet.BillingEvent.impressions,
            AdSet.Field.optimization_goal: AdSet.OptimizationGoal.link_clicks,
            AdSet.Field.bid_strategy: "LOWEST_COST_WITHOUT_CAP",
            AdSet.Field.targeting: {"geo_locations": {"countries": ["IN"]}, "age_min": 18, "age_max": 50},
            AdSet.Field.status: AdSet.Status.paused,
        })
        adset.remote_create()

        ad_img = AdImage(parent_id=acc_id)
        ad_img[AdImage.Field.filename] = "final_ad_poster.png"
        ad_img.remote_create()

        creative = AdCreative(parent_id=acc_id)
        creative.update({
            AdCreative.Field.name: f"{req.brand_name} Creative",
            AdCreative.Field.object_story_spec: {
                "page_id": req.page_id,
                "link_data": {
                    "image_hash": ad_img[AdImage.Field.hash],
                    "link": req.website_url,
                    "message": req.caption,
                    "name": req.headline,
                    "call_to_action": {"type": "LEARN_MORE"},
                },
            },
        })
        creative.remote_create()

        ad = Ad(parent_id=acc_id)
        ad.update({
            Ad.Field.name: f"{req.brand_name} Ad",
            Ad.Field.adset_id: adset.get_id(),
            Ad.Field.creative: {"creative_id": creative.get_id()},
            Ad.Field.status: Ad.Status.paused,
        })
        ad.remote_create()

        return {"status": "success", "campaign_id": campaign.get_id()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
