FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY PRODUCT.md SPEC.md README.md ./

EXPOSE 8451
ENV PORT=8451
CMD ["python3", "app/app.py"]
