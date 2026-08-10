package com.tracemind.order.controller;

import com.tracemind.order.service.OrderService;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/api/orders")
public class OrderController {
    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    @PostMapping("/{orderId}/check-stock")
    public Map<String, Object> checkStock(@PathVariable long orderId,
                                          @RequestBody Map<String, Object> body) {
        long skuId = ((Number) body.get("skuId")).longValue();
        long warehouseId = ((Number) body.get("warehouseId")).longValue();
        int quantity = ((Number) body.get("quantity")).intValue();
        boolean sufficient = orderService.checkStock(orderId, skuId, warehouseId, quantity);
        return Map.of("orderId", orderId, "sufficient", sufficient);
    }
}
