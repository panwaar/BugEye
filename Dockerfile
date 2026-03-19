FROM python:3.11-slim

# Install git — required for cloning repos
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 7860

ENV FLASK_APP=app.py
ENV FLASK_ENV=production

CMD ["python", "app.py"]