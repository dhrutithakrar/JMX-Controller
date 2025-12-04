from app.app import app

# Vercel requires this handler
def handler(request):
    return app(request.environ, start_response)
