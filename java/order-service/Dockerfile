# order-service 构建(上下文 = java/,dockerfile: order-service/Dockerfile)
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /src
ARG OTEL_JAVA_AGENT_VERSION=2.12.0
ARG OTEL_JAVA_AGENT_SHA256=bc7da8dd8789e396cb9547b671ee4eb64fdde0417a8393fe1f9279aa233b5599
# 固定版本 + SHA-256 校验的 OTel Java Agent(阿里云 Maven 镜像;VM 无法直连 GitHub)
RUN curl -fsSL -o /tmp/otel-agent.jar \
      "https://maven.aliyun.com/repository/public/io/opentelemetry/javaagent/opentelemetry-javaagent/${OTEL_JAVA_AGENT_VERSION}/opentelemetry-javaagent-${OTEL_JAVA_AGENT_VERSION}.jar" \
    && echo "${OTEL_JAVA_AGENT_SHA256}  /tmp/otel-agent.jar" | sha256sum -c - \
    && mkdir -p /opt/otel && cp /tmp/otel-agent.jar /opt/otel/opentelemetry-javaagent.jar
COPY pom.xml .
COPY common ./common
COPY order-service ./order-service
COPY inventory-service ./inventory-service
RUN mvn -q -pl order-service -am package -DskipTests

FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=build /src/order-service/target/order-service-*.jar app.jar
COPY --from=build /opt/otel/opentelemetry-javaagent.jar /opt/otel/opentelemetry-javaagent.jar
ENV JAVA_TOOL_OPTIONS="-javaagent:/opt/otel/opentelemetry-javaagent.jar -Xms128m -Xmx256m -XX:MaxMetaspaceSize=128m"
ENV OTEL_SERVICE_NAME=order-service \
    OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317 \
    OTEL_EXPORTER_OTLP_PROTOCOL=grpc \
    OTEL_TRACES_EXPORTER=otlp \
    OTEL_METRICS_EXPORTER=none \
    OTEL_LOGS_EXPORTER=none \
    OTEL_TRACES_SAMPLER=always_on \
    OTEL_PROPAGATORS=tracecontext,baggage \
    OTEL_SEMCONV_STABILITY_OPT_IN=http,database \
    OTEL_RESOURCE_ATTRIBUTES=service.version=1.4.0,deployment.environment.name=demo,service.instance.id=${HOSTNAME}
EXPOSE 8081
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar app.jar"]
