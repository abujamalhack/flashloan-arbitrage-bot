FROM python:3.9-slim

# إعداد بيئة العمل
WORKDIR /app

# تثبيت الاعتمادات النظامية
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# نسخ ملفات المشروع
COPY bot/requirements.txt .
COPY bot/ ./bot/
COPY contracts/ ./contracts/
COPY scripts/ ./scripts/

# تثبيت Python packages
RUN pip install --no-cache-dir -r requirements.txt

# إنشاء مجلدات
RUN mkdir -p /app/logs /app/data

# متغيرات البيئة
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# تعيين المستخدم
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

# نقطة الدخول
CMD ["python", "-m", "bot.main"]
