/**
 * Client-side currency formatting.
 * 
 * Uses Intl.NumberFormat — the browser's built-in locale-aware formatter.
 * This is one of the first localization surfaces we'll audit:
 * - Does the browser format match what the server sends?
 * - Do all locales produce valid output?
 * - Are zero-decimal currencies handled correctly?
 */

const ZERO_DECIMAL_CURRENCIES = new Set([
  'JPY', 'KRW', 'VND', 'CLP', 'PYG', 'UGX', 'RWF',
  'BIF', 'DJF', 'GNF', 'KMF', 'MGA', 'VUV', 'XAF', 'XOF', 'XPF',
]);

/**
 * Format a price for display.
 * @param {number} amountMinor - Amount in smallest currency unit (cents/yen)
 * @param {string} currency - ISO 4217 code (e.g., 'usd')
 * @param {string} locale - BCP 47 locale (e.g., 'en-US', 'fr-CA')
 * @returns {string} Formatted price string
 */
export function formatPrice(amountMinor, currency, locale = 'en-US') {
  const upper = currency.toUpperCase();
  const isZeroDecimal = ZERO_DECIMAL_CURRENCIES.has(upper);
  const amount = isZeroDecimal ? amountMinor : amountMinor / 100;

  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: upper,
      minimumFractionDigits: isZeroDecimal ? 0 : 2,
      maximumFractionDigits: isZeroDecimal ? 0 : 2,
    }).format(amount);
  } catch {
    // Fallback
    return `${upper} ${amount.toFixed(isZeroDecimal ? 0 : 2)}`;
  }
}

/**
 * Get the currency symbol for a given currency.
 */
export function getCurrencySymbol(currency, locale = 'en-US') {
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: currency.toUpperCase(),
      currencyDisplay: 'narrowSymbol',
    }).formatToParts(0).find(p => p.type === 'currency')?.value || currency.toUpperCase();
  } catch {
    return currency.toUpperCase();
  }
}

/**
 * Convert a price from USD cents to another currency's minor units.
 * Mirrors the server-side logic in services/currency.py.
 * Used for display-only — the server does the authoritative conversion at checkout.
 *
 * @param {number} amountUsdCents - Price in USD cents (e.g., 4999 = $49.99)
 * @param {string} currency - Target ISO 4217 code (e.g., 'jpy', 'eur')
 * @returns {number} Amount in target currency's minor units
 */
const MOCK_RATES = {
  usd: 1.0,
  eur: 0.92,
  gbp: 0.79,
  cad: 1.38,
  jpy: 154.5,
  mxn: 17.2,
};

export function convertPrice(amountUsdCents, currency) {
  const cur = (currency || 'usd').toLowerCase();
  if (cur === 'usd') return amountUsdCents;

  const rate = MOCK_RATES[cur] ?? 1.0;
  const usdMajor = amountUsdCents / 100;
  const targetMajor = usdMajor * rate;

  // Zero-decimal currencies: return whole units
  if (ZERO_DECIMAL_CURRENCIES.has(cur.toUpperCase())) {
    return Math.round(targetMajor);
  }
  // Standard currencies: return cents equivalent
  return Math.round(targetMajor * 100);
}
