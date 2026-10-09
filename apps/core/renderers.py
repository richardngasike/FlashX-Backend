"""
Every API response uses one envelope:

    success: {"success": true,  "data": <payload>}
    failure: {"success": false, "error": {"code", "message", "details"}}

Paginated payloads are {"results": [...], "next": <cursor|url|null>, ...}.
"""

from rest_framework.renderers import JSONRenderer


class FlashXJSONRenderer(JSONRenderer):
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        response = (renderer_context or {}).get("response")
        if response is not None and response.status_code == 204:
            return b""
        if isinstance(data, dict) and "success" in data and ("data" in data or "error" in data):
            envelope = data
        elif response is not None and response.status_code >= 400:
            envelope = {"success": False, "error": {"code": "error", "message": "Request failed.", "details": data}}
        else:
            envelope = {"success": True, "data": data}
        return super().render(envelope, accepted_media_type, renderer_context)
