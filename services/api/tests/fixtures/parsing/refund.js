// Hand-written JavaScript fixture for parser and chunker acceptance tests.
export function refund(order, wallet) {
  if (!order.id) {
    throw new Error('Order ID is required')
  }
  return wallet.credit(order.userId, order.amount)
}

export const canRefund = (order) => {
  return Boolean(order.id) && order.amount > 0
}
