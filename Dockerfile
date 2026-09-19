FROM ubuntu:22.04

LABEL maintainer="HTML to APK"
LABEL description="Cloud build service: convert any HTML to a real installable Android APK"

ENV DEBIAN_FRONTEND=noninteractive
ENV PORT=10000
ENV JAVA_HOME=/opt/jdk-21
ENV PATH="/opt/jdk-21/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip wget ca-certificates libz3-4 \
    && rm -rf /var/lib/apt/lists/*

# Install portable Temurin JDK 21
RUN mkdir -p /opt/jdk-21 && \
    wget -q "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.5%2B11/OpenJDK21U-jdk_x64_linux_hotspot_21.0.5_11.tar.gz" -O /tmp/jdk.tgz && \
    tar xzf /tmp/jdk.tgz -C /opt/jdk-21 --strip-components=1 && \
    rm /tmp/jdk.tgz

WORKDIR /app

# Copy backend
COPY backend/ /app/

# Install Python deps
RUN pip3 install --no-cache-dir -r requirements.txt

EXPOSE 10000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD wget -qO- http://localhost:10000/api/health || exit 1

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--workers", "1", "--timeout", "600"]
