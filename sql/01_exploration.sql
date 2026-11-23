-- Superstore retail sales analysis | PostgreSQL


-- Check the import
SELECT
    COUNT(*) AS order_lines,
    COUNT(DISTINCT row_id) AS distinct_row_ids,
    COUNT(DISTINCT order_id) AS distinct_orders,
    MIN(order_date) AS first_order_date,
    MAX(order_date) AS last_order_date
FROM public.orders;

-- Validation and data quality check
SELECT
    COUNT(*) AS order_lines,
    COUNT(DISTINCT row_id) AS distinct_row_ids,
    COUNT(row_id) - COUNT(DISTINCT row_id) AS extra_rows_with_repeated_ids,
    COUNT(DISTINCT order_id) AS distinct_orders,
    COUNT(DISTINCT customer_id) AS distinct_customers,
    MIN(order_date) AS first_order_date,
    MAX(order_date) AS last_order_date,
    COUNT(*) FILTER (WHERE row_id IS NULL) AS missing_row_ids,
    COUNT(*) FILTER (WHERE order_id IS NULL) AS missing_order_ids,
    COUNT(*) FILTER (WHERE customer_id IS NULL) AS missing_customer_ids,
    COUNT(*) FILTER (WHERE product_id IS NULL) AS missing_product_ids,
    COUNT(*) FILTER (WHERE order_date IS NULL) AS missing_order_dates,
    COUNT(*) FILTER (WHERE sales IS NULL) AS missing_sales,
    COUNT(*) FILTER (WHERE profit IS NULL) AS missing_profit,
    COUNT(*) FILTER (WHERE discount IS NULL) AS missing_discounts,
    COUNT(*) FILTER (WHERE ship_date < order_date) AS shipped_before_order,
    MIN(discount) AS min_discount,
    MAX(discount) AS max_discount
FROM public.orders;

-- Discount values (type check)
SELECT discount, COUNT(*) AS order_lines
FROM public.orders
GROUP BY discount
ORDER BY discount;

-- Calculation of overall sales, profit, margin and average order value
SELECT
    COALESCE(SUM(sales), 0) AS total_sales,

    COALESCE(SUM(profit), 0) AS total_profit,

    COUNT(DISTINCT order_id) AS distinct_orders,

    ROUND(
        COALESCE(SUM(profit), 0) /
        NULLIF(SUM(sales), 0) * 100,
        2
    ) AS profit_margin_percentage,

    ROUND(
        SUM(sales) /
        NULLIF(COUNT(DISTINCT order_id), 0),
        2
    ) AS average_sales_per_order
FROM public.orders;

-- Profitability by category and sub-category
SELECT
    category,
    ROUND(SUM(sales), 2) AS total_sales,
    ROUND(SUM(profit), 2) AS total_profit,
    ROUND(100 * SUM(profit) / NULLIF(SUM(sales), 0), 2) AS margin_pct
FROM public.orders
GROUP BY category
ORDER BY SUM(profit) ASC;

SELECT
    category,
    sub_category,
    ROUND(SUM(sales), 2) AS total_sales,
    ROUND(SUM(profit), 2) AS total_profit,
    ROUND(100 * SUM(profit) / NULLIF(SUM(sales), 0), 2) AS margin_pct
FROM public.orders
GROUP BY category, sub_category
ORDER BY SUM(profit) ASC;

-- Comparison ofprofitability by discount level.
WITH discount_analysis AS (
    SELECT
        CASE
            
            WHEN discount = 0 THEN 'No discount'
            WHEN discount > 0 AND discount < 0.20
                THEN 'Low discount'
            WHEN discount >= 0.20 AND discount < 0.30
                THEN 'Moderate discount'
            WHEN discount >= 0.30
                THEN 'High discount'
        END AS discount_band,

        CASE
           
            WHEN discount = 0 THEN 1
            WHEN discount > 0 AND discount < 0.20 THEN 2
            WHEN discount >= 0.20 AND discount < 0.30 THEN 3
            WHEN discount >= 0.30 THEN 4
        END AS band_order,

        sales,
        profit

    FROM public.orders

    WHERE sub_category = 'Tables'
)

SELECT
    discount_band,

    COUNT(*) AS order_lines,

    ROUND(
        COALESCE(SUM(sales), 0)::numeric,
        2
    ) AS total_sales,

    ROUND(
        COALESCE(SUM(profit), 0)::numeric,
        2
    ) AS total_profit,

    ROUND(
        SUM(profit) / NULLIF(SUM(sales), 0) * 100,
        2
    ) AS profit_margin_percentage

FROM discount_analysis

GROUP BY
    discount_band,
    band_order

ORDER BY
    band_order;

-- Summary of sales, profit and margin by year.
SELECT
    EXTRACT(YEAR FROM order_date) AS order_year,
    ROUND(SUM(sales), 2) AS total_sales,
    ROUND(SUM(profit), 2) AS total_profit,
    ROUND(100 * SUM(profit) / NULLIF(SUM(sales), 0), 2) AS margin_pct
FROM public.orders
GROUP BY EXTRACT(YEAR FROM order_date)
ORDER BY order_year ASC;

