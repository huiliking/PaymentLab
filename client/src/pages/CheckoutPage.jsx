import React, { useState, useEffect } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { Elements, PaymentElement, useStripe, useElements } from '@stripe/react-stripe-js'
import { createPaymentIntent } from '../utils/api'
import { formatPrice, convertPrice } from '../utils/currency'
import AddressForm from './AddressForm'

/**
 * Inner form component — must be inside <Elements> to use useStripe/useElements.
 * Uses PaymentElement (v6) instead of CardElement (v2).
 * PaymentElement automatically shows card fields and any other payment methods
 * enabled on your Stripe account, localized to the customer's locale.
 */
function PaymentForm({ email, setEmail, billingCountry, setBillingCountry,
                       billingAddress, setBillingAddress,
                       currency, locale, cartTotal, orderId, paymentIntentId,
                       clearCart }) {
  const stripe = useStripe()
  const elements = useElements()
  const navigate = useNavigate()

  const [processing, setProcessing] = useState(false)
  const [error, setError] = useState(null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!stripe || !elements) return

    setProcessing(true)
    setError(null)

    try {
      // Submit the form to trigger validation
      const { error: submitError } = await elements.submit()
      if (submitError) {
        setError(submitError.message)
        setProcessing(false)
        return
      }

      // Confirm payment — card info goes directly to Stripe, never our server
      const result = await stripe.confirmPayment({
        elements,
        clientSecret: undefined, // already set via Elements provider
        confirmParams: {
          return_url: window.location.origin + `/confirmation?payment_intent=${paymentIntentId}&order=${orderId}`,
          payment_method_data: {
            billing_details: {
              email,
              address: {
                country: billingAddress?.country || billingCountry || undefined,
                line1: billingAddress?.line1 || undefined,
                line2: billingAddress?.line2 || undefined,
                city: billingAddress?.city || undefined,
                state: billingAddress?.state || undefined,
                postal_code: billingAddress?.postalCode || undefined,
              },
              name: billingAddress?.name || undefined,
            },
          },
        },
        redirect: 'if_required',
      })

      if (result.error) {
        setError(result.error.message)
        setProcessing(false)
      } else if (result.paymentIntent?.status === 'succeeded') {
        clearCart()
        navigate(`/confirmation?payment_intent=${paymentIntentId}&order=${orderId}`)
      }
    } catch (err) {
      setError(err.message)
      setProcessing(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="card animate-in">
      <h2 style={{ fontSize: '1.1rem', fontWeight: 500, marginBottom: '1.25rem' }}>Payment details</h2>

      {/* Email */}
      <div style={{ marginBottom: '1rem' }}>
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          value={email}
          onChange={e => setEmail(e.target.value)}
          placeholder="you@example.com"
          required
        />
      </div>

      {/* Billing address — locale-aware full address form */}
      <div style={{ marginBottom: '1rem' }}>
        <label>Billing address</label>
        <AddressForm
          locale={locale}
          onChange={(addr) => {
            setBillingAddress(addr)
            setBillingCountry(addr.country || '')
          }}
        />
      </div>

      {/* Stripe PaymentElement — replaces CardElement in v6 */}
      <div style={{ marginBottom: '1.25rem' }}>
        <label>Payment method</label>
        <PaymentElement options={{
          layout: 'tabs',
        }} />
        <p style={{ fontSize: '0.75rem', color: 'var(--ink-muted)', marginTop: '0.35rem', fontFamily: 'var(--font-mono)' }}>
          Test card: 4242 4242 4242 4242 &middot; any future date &middot; any CVC
        </p>
      </div>

      {/* Error display */}
      {error && (
        <div style={{
          padding: '0.75rem',
          background: 'var(--danger-soft)',
          color: 'var(--danger)',
          borderRadius: 'var(--radius-sm)',
          fontSize: '0.85rem',
          marginBottom: '1rem',
        }}>
          {error}
        </div>
      )}

      {/* Submit */}
      <button
        type="submit"
        className="btn btn-primary"
        disabled={!stripe || processing}
        style={{ width: '100%', padding: '0.8rem' }}
      >
        {processing ? (
          <><div className="spinner" /> Processing...</>
        ) : (
          <>Pay {formatPrice(convertPrice(cartTotal, currency), currency, locale)}</>
        )}
      </button>

      {/* Locale debug info */}
      <div style={{
        marginTop: '1rem',
        padding: '0.75rem',
        background: 'var(--surface-elevated)',
        borderRadius: 'var(--radius-sm)',
        fontSize: '0.75rem',
        fontFamily: 'var(--font-mono)',
        color: 'var(--ink-muted)',
        lineHeight: 1.8,
      }}>
        <strong>Debug — locale signals sent to server:</strong><br />
        locale: {locale}<br />
        currency: {currency}<br />
        billing_country: {billingCountry || '(not set)'}<br />
        browser_language: {navigator.language}
      </div>
    </form>
  )
}


/**
 * Outer checkout page — handles PaymentIntent creation,
 * then renders Elements + PaymentForm once clientSecret is ready.
 */
export default function CheckoutPage({ stripePromise, cart, cartTotal, currency, locale, removeFromCart, clearCart }) {
  const [clientSecret, setClientSecret] = useState(null)
  const [orderId, setOrderId] = useState(null)
  const [paymentIntentId, setPaymentIntentId] = useState(null)
  const [email, setEmail] = useState('')
  const [billingCountry, setBillingCountry] = useState('')
  const [billingAddress, setBillingAddress] = useState(null)
  const [initError, setInitError] = useState(null)
  const [loading, setLoading] = useState(false)

  // Create PaymentIntent when cart is ready and user clicks "Proceed"
  const initializePayment = async () => {
    setLoading(true)
    setInitError(null)
    try {
      const data = await createPaymentIntent({
        items: cart.map(item => ({ id: item.id, quantity: item.quantity })),
        currency,
        customerEmail: email,
        locale,
        billingCountry,
      })
      setClientSecret(data.client_secret)
      setOrderId(data.order_id)
      setPaymentIntentId(data.payment_intent_id)
    } catch (err) {
      setInitError(err.message)
    } finally {
      setLoading(false)
    }
  }

  if (cart.length === 0) {
    return (
      <div>
        <div className="page-header">
          <h1>Checkout</h1>
        </div>
        <div className="card" style={{ textAlign: 'center', padding: '3rem' }}>
          <p style={{ color: 'var(--ink-muted)', marginBottom: '1rem' }}>Your cart is empty</p>
          <Link to="/" className="btn btn-primary">Browse products</Link>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="page-header">
        <h1>Checkout</h1>
        <p>Review your order and pay</p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 380px', gap: '1.5rem', alignItems: 'start' }}>
        {/* Left column: payment form or pre-payment form */}
        {clientSecret ? (
          /* PaymentIntent created — show Stripe Elements */
          <Elements
            stripe={stripePromise}
            options={{
              clientSecret,
              locale: locale.split('-')[0],
              appearance: {
                theme: 'stripe',
                variables: {
                  fontFamily: '"DM Sans", system-ui, sans-serif',
                  colorPrimary: '#7c6ef0',
                },
              },
            }}
          >
            <PaymentForm
              email={email}
              setEmail={setEmail}
              billingCountry={billingCountry}
              setBillingCountry={setBillingCountry}
              billingAddress={billingAddress}
              setBillingAddress={setBillingAddress}
              currency={currency}
              locale={locale}
              cartTotal={cartTotal}
              orderId={orderId}
              paymentIntentId={paymentIntentId}
              clearCart={clearCart}
            />
          </Elements>
        ) : (
          /* Pre-payment: collect email + country, then initialize */
          <div className="card animate-in">
            <h2 style={{ fontSize: '1.1rem', fontWeight: 500, marginBottom: '1.25rem' }}>Your details</h2>

            <div style={{ marginBottom: '1rem' }}>
              <label htmlFor="email">Email</label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="you@example.com"
                required
              />
            </div>

            <div style={{ marginBottom: '1.25rem' }}>
              <label>Billing address</label>
              <AddressForm
                locale={locale}
                onChange={(addr) => {
                  setBillingAddress(addr)
                  setBillingCountry(addr.country || '')
                }}
              />
            </div>

            {initError && (
              <div style={{
                padding: '0.75rem',
                background: 'var(--danger-soft)',
                color: 'var(--danger)',
                borderRadius: 'var(--radius-sm)',
                fontSize: '0.85rem',
                marginBottom: '1rem',
              }}>
                {initError}
              </div>
            )}

            <button
              className="btn btn-primary"
              disabled={!email || !billingCountry || !billingAddress || loading}
              onClick={initializePayment}
              style={{ width: '100%', padding: '0.8rem' }}
            >
              {loading ? (
                <><div className="spinner" /> Preparing payment...</>
              ) : (
                <>Continue to payment</>
              )}
            </button>
          </div>
        )}

        {/* Right column: order summary */}
        <div className="card animate-in" style={{ animationDelay: '0.1s' }}>
          <h2 style={{ fontSize: '1.1rem', fontWeight: 500, marginBottom: '1rem' }}>Order summary</h2>

          {cart.map(item => (
            <div key={item.id} style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '0.6rem 0',
              borderBottom: '1px solid var(--border)',
            }}>
              <div>
                <p style={{ fontSize: '0.9rem', fontWeight: 500 }}>{item.name}</p>
                <p style={{ fontSize: '0.78rem', color: 'var(--ink-muted)' }}>Qty: {item.quantity}</p>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.9rem' }}>
                  {formatPrice(convertPrice(item.price * item.quantity, currency), currency, locale)}
                </span>
                <button
                  onClick={() => removeFromCart(item.id)}
                  style={{
                    background: 'none', border: 'none', color: 'var(--ink-muted)',
                    cursor: 'pointer', fontSize: '1.1rem', lineHeight: 1,
                  }}
                  title="Remove"
                >
                  &times;
                </button>
              </div>
            </div>
          ))}

          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            marginTop: '1rem',
            paddingTop: '0.75rem',
            fontWeight: 600,
          }}>
            <span>Total</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '1.1rem' }}>
              {formatPrice(convertPrice(cartTotal, currency), currency, locale)}
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
