def refund(order_id):
    if not order_id:
        raise ValueError("Order ID is required")
    return "accepted"
