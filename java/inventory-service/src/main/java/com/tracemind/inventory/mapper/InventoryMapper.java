package com.tracemind.inventory.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.tracemind.inventory.entity.Inventory;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface InventoryMapper extends BaseMapper<Inventory> {
    @Select("SELECT id, sku_id, warehouse_id, quantity, version FROM inventory " +
            "WHERE sku_id = #{skuId} AND warehouse_id = #{warehouseId} FOR SHARE")
    Inventory selectBySkuAndWarehouse(@Param("skuId") long skuId,
                                      @Param("warehouseId") long warehouseId);
}
