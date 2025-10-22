import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Any, Dict, List, Optional, Union
from config import API_BASE, WP_API_TOKEN

# Централизованный api_url только здесь
def api_url(path: str) -> str:
    return f"{API_BASE}/{path.lstrip('/')}"

def _auth_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if WP_API_TOKEN:
        headers["Authorization"] = f"Bearer {WP_API_TOKEN}"
    return headers

# Общая Session с ретраями для API-запросов
SESSION = requests.Session()
_retry = Retry(
    total=3, connect=3, read=3, backoff_factor=0.5,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET", "POST"])
)
SESSION.mount("https://", HTTPAdapter(max_retries=_retry))
SESSION.mount("http://", HTTPAdapter(max_retries=_retry))

def _safe_json(resp: requests.Response) -> Union[Dict[str, Any], List[Any], None]:
    try:
        return resp.json()
    except Exception:
        return None

def _as_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        for k in ("data", "items", "results", "courseworks", "teachers", "students"):
            v = data.get(k)
            if isinstance(v, list):
                data = v
                break
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]

def get_teachers() -> List[Dict[str, Any]]:
    try:
        r = SESSION.get(api_url("teachers"), headers=_auth_headers(), timeout=15)
        return _as_list(_safe_json(r)) if r.status_code == 200 else []
    except Exception as e:
        print(f"teachers error: {e}")
        return []

def get_teacher(teacher_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    if not teacher_id:
        return None
    try:
        r = SESSION.get(api_url(f"teacher/{teacher_id}"), headers=_auth_headers(), timeout=15)
        return _safe_json(r) if r.status_code == 200 else None
    except Exception as e:
        print(f"teacher {teacher_id} error: {e}")
        return None

def get_student(student_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    if not student_id:
        return None
    try:
        r = SESSION.get(api_url(f"student/{student_id}"), headers=_auth_headers(), timeout=15)
        return _safe_json(r) if r.status_code == 200 else None
    except Exception as e:
        print(f"student {student_id} error: {e}")
        return None

def get_courseworks() -> List[Dict[str, Any]]:
    try:
        r = SESSION.get(api_url("courseworks"), headers=_auth_headers(), timeout=20)
        js = _safe_json(r) if r.status_code == 200 else []
        return _as_list(js)
    except Exception as e:
        print(f"courseworks error: {e}")
        return []

def get_coursework(cw_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    if not cw_id:
        return None
    try:
        r = SESSION.get(api_url(f"coursework/{cw_id}"), headers=_auth_headers(), timeout=15)
        return _safe_json(r) if r.status_code == 200 else None
    except Exception as e:
        print(f"coursework {cw_id} error: {e}")
        return None

def update_coursework(cw_id: Union[str, int], status: str, grade: Optional[int] = None, comment: Optional[str] = None) -> bool:
    payload = {"id": cw_id, "status": status}
    if grade is not None:
        payload["grade"] = grade
    if comment:
        payload["comment"] = comment
    try:
        r = SESSION.post(api_url("coursework/edit"), json=payload, headers=_auth_headers(), timeout=15)
        return r.status_code == 200
    except Exception as e:
        print(f"edit coursework {cw_id} error: {e}")
        return False
