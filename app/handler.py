from mangum import Mangum

from app.main import create_app

# AWS Lambda entrypoint
handler = Mangum(create_app())
