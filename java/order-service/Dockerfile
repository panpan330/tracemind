# order-service 构建(上下文 = java/,dockerfile: order-service/Dockerfile)
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY pom.xml .
COPY common ./common
COPY order-service ./order-service
COPY inventory-service ./inventory-service
RUN mvn -q -pl order-service -am package -DskipTests

FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=build /src/order-service/target/order-service-*.jar app.jar
ENV JAVA_OPTS="-Xmx256m -XX:+UseSerialGC"
EXPOSE 8081
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar app.jar"]
