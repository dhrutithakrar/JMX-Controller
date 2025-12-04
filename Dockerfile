FROM eclipse-temurin:11-jre

ARG JMETER_VERSION=5.6.2
ENV JMETER_HOME=/opt/jmeter
ENV PATH=$JMETER_HOME/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends       wget unzip python3 python3-pip python3-venv ca-certificates &&     update-ca-certificates &&     wget https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-${JMETER_VERSION}.zip &&     unzip apache-jmeter-${JMETER_VERSION}.zip -d /opt &&     mv /opt/apache-jmeter-${JMETER_VERSION} /opt/jmeter &&     rm -f apache-jmeter-${JMETER_VERSION}.zip

WORKDIR /app
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt --break-system-packages
COPY app/ /app/app/
COPY app/templates/ /app/templates/
COPY app/static/ /app/static/
EXPOSE 5000
CMD ["python3", "-m", "app.app"]