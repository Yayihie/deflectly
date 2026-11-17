FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY PRODUCT.md SPEC.md README.md ./

# Railway/Render inject PORT; default to 8451 for local runs
ENV PORT=8451
EXPOSE 8451
CMD ["sh", "-c", "gunicorn app.app:app --bind 0.0.0.0:${PORT} --workers 2 --timeout 120 --access-logfile -"]
