FROM mashrequae.azurecr.io/chatapi-baseimage:v1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY ai_extractor.py .
COPY azure_devops.py .
COPY database.py .
COPY pdf_utils.py .
COPY pipeline_mapping.json .
COPY build_pipeline_mapping.json .

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
