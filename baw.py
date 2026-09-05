"""
Barone-Adesi & Whaley (1987) American option pricing, and its European baseline.

WHY WE NEED AN AMERICAN PRICER AT ALL
-------------------------------------
SPY options are American style: the holder may exercise on any day up to expiry, not only
at expiry. That optionality is worth something, so an American option is never worth less
than the otherwise identical European one. Black-Scholes prices only the European case, so
using it on SPY would systematically understate value -- and, more importantly for a
margin model, would understate how that value moves.

There is no closed-form solution for the American price. The exact answer requires a
numerical method (a lattice, or a PDE solve) at every strike and every scenario, which is
why the FICC/NYPC filing specifies the Barone-Adesi & Whaley *approximation* instead: it is
a closed form, accurate to a cent or so over the region that matters, and fast enough to
run inside a full-revaluation loop over hundreds of scenarios.

HOW THE APPROXIMATION WORKS, IN WORDS
-------------------------------------
The American price is written as the European price plus an EARLY EXERCISE PREMIUM:

    American  =  European  +  premium

Both the American and the European value satisfy the same Black-Scholes PDE, so their
difference -- the premium -- satisfies it too. BAW's move is to drop one term from the PDE
for that difference (the term measuring how the premium decays with time), which turns an
intractable PDE into an ordinary differential equation with a power-law solution
`A * S^q`. That is where the quadratic and its roots `q1`, `q2` come from, and it is why
this is called the "quadratic approximation".

The remaining task is to find the CRITICAL PRICE: the underlying level at which the holder
should stop holding and exercise immediately. Above it (for a call) the option is simply
worth its intrinsic value; below it, it is worth the European price plus the premium term.
That boundary has no closed form either, so we solve for it by Newton-Raphson --
`solve_critical_price` below.

COST OF CARRY
-------------
Every function here takes a cost of carry `b`, defined by `F = S * exp(b * T)`. This one
parameter covers every case we care about:

    b = r        non-dividend-paying stock (classic Black-Scholes)
    b = r - q    stock or ETF paying a continuous dividend yield q
    b = 0        futures option (Black-76)

# NOTE (deviation from filing): the original SR-FICC-2013-02 filing prices options on
# interest rate FUTURES, where b = 0 and the model reduces to Black-76. SPY is a
# dividend-paying equity ETF, so b = r - q instead. We implement the general b form and
# derive b from the market itself in Phase 0 rather than assuming a dividend yield -- see
# `data_loader.imply_forward_from_parity`. For our reference expiry b came out at 5.5926%,
# essentially equal to r, because no SPY ex-dividend date falls inside the window.

Run this file directly to print the full validation suite:

    python3 baw.py
"""

import numpy as np
from scipy.stats import norm


# Newton-Raphson settings for the critical price solve. The tolerance is relative to the
# strike, so it means "converged to within a millionth of the strike" -- about $0.0005 on
# a $500 strike, far finer than any price we report.
CRITICAL_PRICE_TOLERANCE = 1e-6
CRITICAL_PRICE_MAX_ITERATIONS = 100


def black_scholes_price(underlying_price, strike, time_to_expiry, risk_free_rate,
                        cost_of_carry, volatility, option_type):
    """
    Generalised Black-Scholes-Merton European option price with a cost of carry.

    This is the European baseline the BAW early-exercise premium is added on top of. With
    `cost_of_carry = 0` it is exactly Black-76, the futures-option form the filing uses.

    THE TWO d TERMS
    ---------------
    `d2` is the number of standard deviations, in log space, by which the forward exceeds
    the strike. `N(d2)` is therefore the risk-neutral probability the option finishes in
    the money -- the probability we actually pay the strike.

    `d1` is `d2` plus one volatility unit, `sigma * sqrt(T)`. `N(d1)` is not a probability;
    it is the expected value of the underlying at expiry conditional on finishing in the
    money, expressed as a fraction of the forward. It is also the option's delta (times the
    carry discount factor).

    So the call formula reads: "what I expect to receive if it finishes in the money, minus
    what I expect to pay, each discounted back."

    Inputs:
        underlying_price (float):  spot price S, in dollars.
        strike (float):            strike K, in dollars.
        time_to_expiry (float):    T, in years. Must be > 0.
        risk_free_rate (float):    r, continuously compounded annual rate, decimal.
        cost_of_carry (float):     b, defined by F = S * exp(b * T), decimal.
        volatility (float):        sigma, annualised, decimal (0.20 means 20%).
        option_type (str):         'call' or 'put'.

    Returns:
        float: the European option price, in dollars.
    """
    _validate_pricing_inputs(underlying_price, strike, time_to_expiry, volatility,
                             option_type)

    volatility_root_time = volatility * np.sqrt(time_to_expiry)

    # d1 and d2 as described above. Note that the drift term uses b, not r: under the
    # risk-neutral measure the underlying grows at the cost of carry, while cash flows are
    # discounted at r. Conflating the two is the classic dividend-handling bug.
    d1 = (
        np.log(underlying_price / strike)
        + (cost_of_carry + 0.5 * volatility ** 2) * time_to_expiry
    ) / volatility_root_time
    d2 = d1 - volatility_root_time

    # exp((b - r) * T) converts the spot into a discounted forward. When b = r (no
    # dividend) it is 1 and the underlying term is just S * N(d1).
    carry_discount = np.exp((cost_of_carry - risk_free_rate) * time_to_expiry)
    rate_discount = np.exp(-risk_free_rate * time_to_expiry)

    if option_type == "call":
        return (
            underlying_price * carry_discount * norm.cdf(d1)
            - strike * rate_discount * norm.cdf(d2)
        )

    return (
        strike * rate_discount * norm.cdf(-d2)
        - underlying_price * carry_discount * norm.cdf(-d1)
    )


def solve_critical_price(strike, time_to_expiry, risk_free_rate, cost_of_carry,
                         volatility, option_type):
    """
    Solve for the critical underlying price at which immediate exercise becomes optimal.

    WHAT THIS NUMBER MEANS ECONOMICALLY
    -----------------------------------
    Holding an option instead of exercising it has a benefit and a cost. The benefit is
    that you keep the optionality -- if the market moves against you, you simply do not
    exercise. The cost is that you forgo something: for a put, the interest you could be
    earning on the strike proceeds; for a call, the dividends you would receive by owning
    the shares.

    The critical price `S*` is where those exactly balance. For a CALL, `S*` sits ABOVE the
    strike: only when the underlying has risen far enough do the forgone dividends outweigh
    the remaining optionality. For a PUT, `S**` sits BELOW the strike: only when the
    underlying has fallen far enough does the interest on the strike outweigh it.

    Beyond that boundary the option is worth exactly its intrinsic value, because a
    rational holder has already exercised.

    THE SOLVE
    ---------
    At the boundary the two branches of the BAW solution must meet: the intrinsic value
    must equal the European price plus the premium term. That gives one equation in one
    unknown, with no closed form. We use Newton-Raphson from the Barone-Adesi & Whaley
    seed value -- an analytic first guess built from the infinite-maturity solution, which
    is close enough that convergence typically takes only a few iterations.

    Inputs:
        strike (float):          K, in dollars.
        time_to_expiry (float):  T, in years.
        risk_free_rate (float):  r, decimal.
        cost_of_carry (float):   b, decimal.
        volatility (float):      sigma, decimal.
        option_type (str):       'call' or 'put'.

    Returns:
        float: the critical price, in dollars. Above it for a call, below it for a put,
        immediate exercise is optimal.

    Raises:
        RuntimeError: if Newton-Raphson fails to converge within the iteration cap. We
        raise rather than return the last iterate, because a silently unconverged boundary
        produces prices that look plausible and are wrong.
    """
    _validate_pricing_inputs(strike, strike, time_to_expiry, volatility, option_type)

    # For a call with b >= r there is NO finite exercise boundary: early exercise is never
    # optimal at any underlying level, so the boundary sits at infinity and there is
    # nothing to solve for. `baw_price` short-circuits this case before ever reaching
    # here, but a direct caller deserves to be told why rather than watching Newton churn
    # through its iteration cap and report a misleading convergence failure.
    if option_type == "call" and cost_of_carry >= risk_free_rate:
        raise ValueError(
            f"A call with b >= r (here b={cost_of_carry}, r={risk_free_rate}) has no "
            f"finite critical price -- early exercise is never optimal, so the American "
            f"call equals the European call. Call `baw_price`, which handles this."
        )

    variance = volatility ** 2
    root_time = np.sqrt(time_to_expiry)

    # The three quantities the filing and the paper both name explicitly.
    # M and N are the PDE coefficients rescaled by variance; K_baw is the fraction of the
    # option's life that has "used up" its discounting, and it is what makes the roots
    # depend on maturity at all.
    m = 2.0 * risk_free_rate / variance
    n = 2.0 * cost_of_carry / variance
    k_baw = 1.0 - np.exp(-risk_free_rate * time_to_expiry)

    # ---- The seed value ---------------------------------------------------------------
    # As T -> infinity, K_baw -> 1 and the roots take a simpler form. The perpetual option
    # has a known critical price, and BAW build their starting guess by interpolating from
    # it back to the finite-maturity case through an exponential term. This is the
    # "Barone-Adesi-Whaley seed" the brief refers to.
    if option_type == "call":
        q2_perpetual = (-(n - 1.0) + np.sqrt((n - 1.0) ** 2 + 4.0 * m)) / 2.0
        critical_price_perpetual = strike / (1.0 - 1.0 / q2_perpetual)

        h2 = -(cost_of_carry * time_to_expiry + 2.0 * volatility * root_time) * (
            strike / (critical_price_perpetual - strike)
        )
        critical_price = strike + (critical_price_perpetual - strike) * (1.0 - np.exp(h2))
    else:
        q1_perpetual = (-(n - 1.0) - np.sqrt((n - 1.0) ** 2 + 4.0 * m)) / 2.0
        critical_price_perpetual = strike / (1.0 - 1.0 / q1_perpetual)

        h1 = (cost_of_carry * time_to_expiry - 2.0 * volatility * root_time) * (
            strike / (strike - critical_price_perpetual)
        )

        # h1 must be negative for the seed to interpolate between the perpetual boundary
        # and the strike, since exp(h1) is the interpolation weight. It normally is,
        # because the 2*sigma*sqrt(T) term dominates b*T.
        #
        # It stops being true as volatility approaches zero: the perpetual boundary
        # collapses onto the strike, so the denominator (K - S_perpetual) goes to zero and
        # h1 blows up to +infinity, overflowing exp and producing a NaN seed. This is a
        # real degenerate limit rather than a coding slip -- with no volatility there is no
        # reason to wait, so the exercise boundary IS the strike. Clamping h1 at zero gives
        # exactly that limit (seed = K) and keeps the solver stable when the inverter in
        # `implied_volatility` probes the bottom of its search bracket.
        h1 = min(h1, 0.0)

        critical_price = critical_price_perpetual + (
            strike - critical_price_perpetual
        ) * np.exp(h1)

    # ---- The finite-maturity root -----------------------------------------------------
    # This is the root that actually appears in the pricing formula. Note it uses
    # 4 * m / k_baw where the perpetual version used 4 * m: dividing by k_baw is precisely
    # what introduces the maturity dependence.
    discriminant = np.sqrt((n - 1.0) ** 2 + 4.0 * m / k_baw)

    if option_type == "call":
        q = (-(n - 1.0) + discriminant) / 2.0
    else:
        q = (-(n - 1.0) - discriminant) / 2.0

    carry_discount = np.exp((cost_of_carry - risk_free_rate) * time_to_expiry)

    # ---- Newton-Raphson ---------------------------------------------------------------
    for _ in range(CRITICAL_PRICE_MAX_ITERATIONS):
        d1 = (
            np.log(critical_price / strike)
            + (cost_of_carry + 0.5 * variance) * time_to_expiry
        ) / (volatility * root_time)

        european = black_scholes_price(
            critical_price, strike, time_to_expiry, risk_free_rate, cost_of_carry,
            volatility, option_type,
        )

        if option_type == "call":
            # At the boundary: (S* - K) must equal european + premium.
            intrinsic = critical_price - strike
            premium_term = (1.0 - carry_discount * norm.cdf(d1)) * critical_price / q
            boundary_value = european + premium_term

            # Derivative of `boundary_value` with respect to S*, used by Newton-Raphson.
            slope = (
                carry_discount * norm.cdf(d1) * (1.0 - 1.0 / q)
                + (1.0 - carry_discount * norm.pdf(d1) / (volatility * root_time)) / q
            )
        else:
            intrinsic = strike - critical_price
            premium_term = (1.0 - carry_discount * norm.cdf(-d1)) * critical_price / q
            boundary_value = european - premium_term

            slope = (
                -carry_discount * norm.cdf(-d1) * (1.0 - 1.0 / q)
                - (1.0 + carry_discount * norm.pdf(-d1) / (volatility * root_time)) / q
            )

        # Converged when the two sides of the boundary condition agree, measured relative
        # to the strike so the criterion is scale free.
        if abs(intrinsic - boundary_value) / strike < CRITICAL_PRICE_TOLERANCE:
            return float(critical_price)

        # The Newton step, rearranged into the form BAW give it in.
        if option_type == "call":
            critical_price = (strike + boundary_value - slope * critical_price) / (
                1.0 - slope
            )
        else:
            critical_price = (strike - boundary_value + slope * critical_price) / (
                1.0 + slope
            )

        # Keep the iterate inside the domain where the boundary can possibly lie. Newton
        # is not globally convergent, and at high volatility on a deep in-the-money option
        # a single overshoot can throw the iterate to a NEGATIVE price -- from which the
        # log in d1 returns NaN and every subsequent step is poisoned.
        #
        # The bounds are economics, not numerical hygiene: a call is exercised early only
        # above the strike, so its boundary must exceed K; a put only below it, so its
        # boundary must lie between zero and K. Clamping into that region turns a
        # divergent step into a safeguarded one, and if the solve genuinely cannot
        # converge it still exhausts the iteration cap and raises below.
        if option_type == "call":
            critical_price = max(critical_price, strike * (1.0 + 1e-10))
        else:
            critical_price = min(max(critical_price, strike * 1e-10),
                                 strike * (1.0 - 1e-10))

    raise RuntimeError(
        f"solve_critical_price failed to converge in {CRITICAL_PRICE_MAX_ITERATIONS} "
        f"iterations for a {option_type} with K={strike}, T={time_to_expiry}, "
        f"r={risk_free_rate}, b={cost_of_carry}, sigma={volatility}. "
        f"Last iterate was {critical_price}."
    )


def baw_price(underlying_price, strike, time_to_expiry, risk_free_rate, cost_of_carry,
              volatility, option_type):
    """
    Barone-Adesi & Whaley (1987) quadratic approximation to the American option price.

    The result is the European price plus an early-exercise premium, except beyond the
    critical price where the option is worth exactly its intrinsic value.

    # NOTE (deviation from filing): `cost_of_carry` is a REQUIRED argument with no
    # default. The brief suggested defaulting it for SPY, but any hardcoded default would
    # conflict with the b we derive from the market in Phase 0 (5.5926% for our reference
    # expiry), and a silently wrong carry produces prices that look entirely plausible.
    # Making it explicit at every call site is the safer choice.

    Inputs:
        underlying_price (float):  spot S, in dollars.
        strike (float):            K, in dollars.
        time_to_expiry (float):    T, in years. Must be > 0.
        risk_free_rate (float):    r, continuously compounded, decimal.
        cost_of_carry (float):     b, where F = S * exp(b * T), decimal. For SPY this is
                                   r - q; for a futures option it is 0.
        volatility (float):        sigma, annualised, decimal.
        option_type (str):         'call' or 'put'.

    Returns:
        float: the American option price, in dollars.
    """
    _validate_pricing_inputs(underlying_price, strike, time_to_expiry, volatility,
                             option_type)

    european = black_scholes_price(
        underlying_price, strike, time_to_expiry, risk_free_rate, cost_of_carry,
        volatility, option_type,
    )

    # ---- The one case with no early exercise value ------------------------------------
    # For a CALL, when b >= r the underlying grows at least as fast as cash does. There is
    # then never a reason to exercise early: you would be paying the strike sooner than
    # necessary and giving up the remaining optionality, in exchange for holding an asset
    # that is not out-growing the interest you forgo. The American call is worth exactly
    # the European call.
    #
    # In our SPY case b = 5.5926% and r = 5.4000%, so b > r and this branch is taken --
    # a direct consequence of no dividend falling inside the reference window. It is also
    # the sharpest available test of a BAW implementation, and the validation suite below
    # checks it explicitly.
    if option_type == "call" and cost_of_carry >= risk_free_rate:
        return european

    critical_price = solve_critical_price(
        strike, time_to_expiry, risk_free_rate, cost_of_carry, volatility, option_type,
    )

    variance = volatility ** 2
    m = 2.0 * risk_free_rate / variance
    n = 2.0 * cost_of_carry / variance
    k_baw = 1.0 - np.exp(-risk_free_rate * time_to_expiry)

    discriminant = np.sqrt((n - 1.0) ** 2 + 4.0 * m / k_baw)

    # q1 and q2 are the two roots of the quadratic that came out of the approximated PDE.
    # q2 > 1 is the root used for calls; q1 < 0 is the root used for puts. Their signs are
    # what make the premium term behave correctly: (S/S*)^q2 grows as S rises towards the
    # call boundary, and (S/S**)^q1 grows as S falls towards the put boundary.
    q1 = (-(n - 1.0) - discriminant) / 2.0
    q2 = (-(n - 1.0) + discriminant) / 2.0

    d1_critical = (
        np.log(critical_price / strike) + (cost_of_carry + 0.5 * variance) * time_to_expiry
    ) / (volatility * np.sqrt(time_to_expiry))

    carry_discount = np.exp((cost_of_carry - risk_free_rate) * time_to_expiry)

    if option_type == "call":
        # A2 is fixed by requiring the price and its slope to match the intrinsic value at
        # the critical price -- the "smooth pasting" condition.
        a2 = (critical_price / q2) * (1.0 - carry_discount * norm.cdf(d1_critical))

        if underlying_price < critical_price:
            return european + a2 * (underlying_price / critical_price) ** q2

        # At or above the boundary a rational holder has exercised, so the option is worth
        # its intrinsic value and nothing more.
        return underlying_price - strike

    a1 = -(critical_price / q1) * (1.0 - carry_discount * norm.cdf(-d1_critical))

    if underlying_price > critical_price:
        return european + a1 * (underlying_price / critical_price) ** q1

    return strike - underlying_price


def binomial_american_price(underlying_price, strike, time_to_expiry, risk_free_rate,
                            cost_of_carry, volatility, option_type, n_steps=2000):
    """
    Cox-Ross-Rubinstein binomial American option price. **Validation reference only.**

    This is not part of the pricing pipeline and is never called by the VaR machinery. It
    exists so that `baw_price` can be checked against an independent method that converges
    to the true American value, which is how Barone-Adesi and Whaley assessed their own
    approximation's accuracy in the original paper.

    The lattice steps the underlying up by `u` or down by `d = 1/u` each period, then walks
    backwards from expiry. At every node it takes the larger of continuing to hold and
    exercising immediately -- and that maximum, applied at every node, is exactly what
    makes the result American rather than European.

    Inputs:
        underlying_price (float):  spot S, in dollars.
        strike (float):            K, in dollars.
        time_to_expiry (float):    T, in years.
        risk_free_rate (float):    r, decimal.
        cost_of_carry (float):     b, decimal.
        volatility (float):        sigma, decimal.
        option_type (str):         'call' or 'put'.
        n_steps (int):             lattice steps. Error falls roughly as 1/n_steps, and
                                   oscillates, so we average two adjacent step counts.

    Returns:
        float: the American option price, in dollars.
    """
    _validate_pricing_inputs(underlying_price, strike, time_to_expiry, volatility,
                             option_type)

    def price_with_steps(steps):
        """Run one lattice at a given number of steps."""
        dt = time_to_expiry / steps

        up_factor = np.exp(volatility * np.sqrt(dt))
        down_factor = 1.0 / up_factor

        # The risk-neutral up-probability uses the cost of carry as the growth rate, while
        # discounting uses r. Same distinction as in Black-Scholes.
        up_probability = (np.exp(cost_of_carry * dt) - down_factor) / (
            up_factor - down_factor
        )
        discount = np.exp(-risk_free_rate * dt)

        # Terminal underlying prices, from all-down at index 0 to all-up at index `steps`.
        up_moves = np.arange(steps + 1)
        terminal_prices = underlying_price * up_factor ** up_moves * down_factor ** (
            steps - up_moves
        )

        if option_type == "call":
            values = np.maximum(terminal_prices - strike, 0.0)
        else:
            values = np.maximum(strike - terminal_prices, 0.0)

        # Walk backwards. At each step the node prices are the previous level's prices
        # with one fewer up-move available, which is why we re-derive them per level.
        for step in range(steps - 1, -1, -1):
            values = discount * (
                up_probability * values[1:] + (1.0 - up_probability) * values[:-1]
            )

            up_moves = np.arange(step + 1)
            node_prices = underlying_price * up_factor ** up_moves * down_factor ** (
                step - up_moves
            )

            if option_type == "call":
                intrinsic = node_prices - strike
            else:
                intrinsic = strike - node_prices

            # THE American step: exercise now if that beats holding on.
            values = np.maximum(values, intrinsic)

        return float(values[0])

    # CRR convergence to the American price oscillates as the lattice alternately does and
    # does not place a node near the exercise boundary. Averaging adjacent step counts
    # cancels most of that oscillation and buys roughly an order of magnitude of accuracy.
    return 0.5 * (price_with_steps(n_steps) + price_with_steps(n_steps + 1))


def _validate_pricing_inputs(underlying_price, strike, time_to_expiry, volatility,
                             option_type):
    """
    Reject inputs that would produce a silently wrong number rather than an error.

    Inputs:
        underlying_price (float), strike (float), time_to_expiry (float),
        volatility (float), option_type (str): as in the pricing functions.

    Returns:
        None. Raises ValueError on a bad input.
    """
    if option_type not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")

    if underlying_price <= 0:
        raise ValueError(f"underlying_price must be positive, got {underlying_price}")

    if strike <= 0:
        raise ValueError(f"strike must be positive, got {strike}")

    # T = 0 and sigma = 0 both divide by zero in d1. They also have well-defined limits
    # (the intrinsic value), but silently returning that would hide a caller passing an
    # expired option into a pricing loop, which is a bug worth surfacing.
    if time_to_expiry <= 0:
        raise ValueError(
            f"time_to_expiry must be positive, got {time_to_expiry}. An expired option "
            f"is worth its intrinsic value; handle that at the call site."
        )

    if volatility <= 0:
        raise ValueError(f"volatility must be positive, got {volatility}")


# ======================================================================================
# VALIDATION
#
# The brief is explicit that validation, not the code, is the deliverable of this phase.
# Each function below tests one property, prints its own table, and returns True if the
# property held. `_run_standalone` runs them all and reports a single verdict.
# ======================================================================================


def check_put_call_parity(tolerance=1e-10):
    """
    European put-call parity: C - P = S*exp((b-r)T) - K*exp(-rT).

    This is not a modelling assumption, it is an arbitrage identity -- a portfolio long a
    call and short a put has exactly the same payoff as a forward, so it must have the same
    price. It should hold to machine precision, because both sides come from the same two
    normal CDFs and the identity N(x) + N(-x) = 1. Any failure here means an algebra error
    in `black_scholes_price`, not an approximation.

    Inputs:
        tolerance (float): largest acceptable absolute violation, in dollars.

    Returns:
        bool: True if parity held for every case tested.
    """
    print("TEST 1: European put-call parity  (expect machine precision)")
    print(f"  {'S':>7} {'K':>7} {'T':>6} {'r':>7} {'b':>7} {'sigma':>7} "
          f"{'C - P':>12} {'forward':>12} {'error':>11}")

    cases = [
        # S,    K,    T,    r,     b,      sigma
        (100.0, 100.0, 0.5, 0.08, 0.08, 0.20),   # no dividend
        (100.0, 100.0, 0.5, 0.08, 0.04, 0.20),   # dividend paying
        (100.0, 100.0, 0.5, 0.08, 0.00, 0.20),   # futures option, Black-76
        (100.0, 100.0, 0.5, 0.08, -0.04, 0.40),  # high dividend, high vol
        (80.0, 120.0, 1.5, 0.03, -0.02, 0.55),   # deep out of the money
        (475.31, 475.0, 0.13425, 0.0540, 0.055926, 0.115),  # our SPY reference
    ]

    worst_error = 0.0

    for underlying_price, strike, time_to_expiry, rate, carry, vol in cases:
        call = black_scholes_price(underlying_price, strike, time_to_expiry, rate, carry,
                                   vol, "call")
        put = black_scholes_price(underlying_price, strike, time_to_expiry, rate, carry,
                                  vol, "put")

        call_minus_put = call - put
        forward_value = (
            underlying_price * np.exp((carry - rate) * time_to_expiry)
            - strike * np.exp(-rate * time_to_expiry)
        )
        error = abs(call_minus_put - forward_value)
        worst_error = max(worst_error, error)

        print(f"  {underlying_price:>7.2f} {strike:>7.2f} {time_to_expiry:>6.4f} "
              f"{rate:>7.4f} {carry:>7.4f} {vol:>7.3f} {call_minus_put:>12.8f} "
              f"{forward_value:>12.8f} {error:>11.2e}")

    passed = worst_error < tolerance
    print(f"  Worst error: {worst_error:.3e}   -> {'PASS' if passed else 'FAIL'}")

    return passed


def check_american_call_equals_european_when_no_dividend(tolerance=1e-12):
    """
    An American call on a non-dividend-paying underlying (b = r) equals the European call.

    THIS IS THE SHARPEST TEST OF A BAW IMPLEMENTATION.

    The economics: exercising a call early means paying the strike sooner than you have to
    and throwing away the remaining optionality, in exchange for owning the underlying. If
    the underlying pays nothing out, owning it early buys you nothing -- so early exercise
    is strictly worse, always, and the early-exercise premium must be exactly zero.

    A great many BAW implementations get this wrong: they compute a critical price anyway,
    find some finite value, and add a small spurious premium. If this test does not pass to
    machine precision, the implementation is wrong regardless of how good the other
    numbers look.

    Inputs:
        tolerance (float): largest acceptable difference, in dollars.

    Returns:
        bool: True if the premium was exactly zero in every case.
    """
    print()
    print("TEST 2: American call = European call when b = r  (no dividend, expect exact)")
    print(f"  {'S':>7} {'K':>7} {'T':>6} {'r=b':>7} {'sigma':>7} "
          f"{'European':>12} {'American':>12} {'premium':>11}")

    cases = [
        (90.0, 100.0, 0.5, 0.08, 0.20),
        (100.0, 100.0, 0.5, 0.08, 0.20),
        (110.0, 100.0, 0.5, 0.08, 0.20),
        (100.0, 100.0, 3.0, 0.10, 0.45),
        (150.0, 100.0, 2.0, 0.12, 0.30),   # deep in the money, long dated, high rate
    ]

    worst_premium = 0.0

    for underlying_price, strike, time_to_expiry, rate, vol in cases:
        # b = r is the defining condition: the underlying pays out nothing.
        european = black_scholes_price(underlying_price, strike, time_to_expiry, rate,
                                       rate, vol, "call")
        american = baw_price(underlying_price, strike, time_to_expiry, rate, rate, vol,
                             "call")

        premium = american - european
        worst_premium = max(worst_premium, abs(premium))

        print(f"  {underlying_price:>7.2f} {strike:>7.2f} {time_to_expiry:>6.2f} "
              f"{rate:>7.4f} {vol:>7.3f} {european:>12.8f} {american:>12.8f} "
              f"{premium:>11.2e}")

    passed = worst_premium < tolerance
    print(f"  Worst spurious premium: {worst_premium:.3e}   "
          f"-> {'PASS' if passed else 'FAIL'}")

    return passed


def check_american_bounds():
    """
    Two inequalities that must hold for every American option, everywhere.

      American >= European   -- the right to exercise early cannot have negative value.
      American >= intrinsic  -- otherwise you could buy the option, exercise it
                                immediately, and book a riskless profit.

    These are weak tests individually but they are exhaustive: we sweep a wide grid, and a
    single violation anywhere is a hard failure. Bugs in the critical-price branch tend to
    show up here first, because that is where the formula switches between the premium
    expression and raw intrinsic value.

    Returns:
        bool: True if both inequalities held at every grid point.
    """
    print()
    print("TEST 3: American >= European and American >= intrinsic  (swept over a grid)")

    n_tested = 0
    european_violations = []
    intrinsic_violations = []

    for underlying_price in [70.0, 85.0, 100.0, 115.0, 130.0]:
        for time_to_expiry in [0.08, 0.5, 2.0]:
            for rate in [0.01, 0.05, 0.10]:
                for carry in [-0.06, -0.02, 0.0, 0.05, 0.10]:
                    for vol in [0.10, 0.25, 0.60]:
                        for option_type in ["call", "put"]:
                            strike = 100.0

                            american = baw_price(underlying_price, strike, time_to_expiry,
                                                 rate, carry, vol, option_type)
                            european = black_scholes_price(underlying_price, strike,
                                                           time_to_expiry, rate, carry,
                                                           vol, option_type)

                            if option_type == "call":
                                intrinsic = max(underlying_price - strike, 0.0)
                            else:
                                intrinsic = max(strike - underlying_price, 0.0)

                            n_tested += 1

                            # A tiny tolerance absorbs floating point noise on the
                            # boundary; anything larger is a real violation.
                            if american < european - 1e-10:
                                european_violations.append(
                                    (underlying_price, time_to_expiry, rate, carry, vol,
                                     option_type, american, european)
                                )

                            if american < intrinsic - 1e-10:
                                intrinsic_violations.append(
                                    (underlying_price, time_to_expiry, rate, carry, vol,
                                     option_type, american, intrinsic)
                                )

    print(f"  Grid points tested            : {n_tested}")
    print(f"  American < European violations: {len(european_violations)}")
    print(f"  American < intrinsic violations: {len(intrinsic_violations)}")

    for violation in (european_violations + intrinsic_violations)[:5]:
        print(f"    VIOLATION: S={violation[0]}, T={violation[1]}, r={violation[2]}, "
              f"b={violation[3]}, sigma={violation[4]}, {violation[5]}: "
              f"{violation[6]:.8f} < {violation[7]:.8f}")

    passed = not european_violations and not intrinsic_violations
    print(f"  -> {'PASS' if passed else 'FAIL'}")

    return passed


def check_against_binomial(relative_tolerance=0.01, absolute_tolerance=0.10,
                           relative_error_price_floor=0.50):
    """
    Compare BAW against an independent, convergent American pricer.

    # NOTE (deviation from filing): the brief asked for a benchmark transcribed from the
    # Barone-Adesi & Whaley (1987) paper's tables or from Haug's book. I have not
    # transcribed those values, because I cannot verify them from this environment and a
    # mis-remembered reference number would make this test worse than useless -- it would
    # "validate" the pricer against fiction. Instead the reference is a 2000-step
    # Cox-Ross-Rubinstein binomial lattice, which converges to the true American value.
    # This is the same methodology Barone-Adesi and Whaley used to assess their own
    # approximation in the original paper, and it covers a whole grid rather than a
    # handful of published points. If you have the tables to hand, they are easy to add.

    The parameter set is the one the BAW paper uses for its accuracy tables: K = 100,
    r = 8%, b = -4% (a 12% dividend yield, chosen so that early exercise genuinely
    matters for calls as well as puts), across two maturities, two volatilities and five
    spot levels. This is deliberately a STRESS grid, far harsher than anything our SPY
    application will see -- see `check_spy_regime_accuracy` for the regime we actually
    operate in.

    WHERE THE TOLERANCES COME FROM
    ------------------------------
    BAW is an approximation, so the question is not "is the error zero" but "is the error
    where the method's known weakness says it should be". It is, and this was verified
    against a 40,000-step lattice while building this suite. For a call at K=100, r=8%,
    b=-4%, sigma=40%, T=0.25, the error tracks distance from the exercise boundary S*:

        S      S/S*    abs err   rel err
        100    0.732   0.0035    0.047%     <- at the money, near exact
        110    0.805   0.0242    0.179%
        120    0.878   0.0598    0.281%
        125    0.915   0.0700    0.273%     <- worst, just below the boundary
        140    1.024   0.0000    0.000%     <- past it, price is just intrinsic

    That is precisely the documented weakness of the quadratic approximation: it is least
    accurate in the region just below the exercise boundary, where the term BAW drop from
    the PDE matters most. It vanishes at the money and vanishes again past the boundary.

    WHAT THIS TEST DOES AND DOES NOT PROVE
    --------------------------------------
    It measures the accuracy of the METHOD, not the correctness of this implementation.
    Correctness is established by tests 1, 2, 3, 5 and 6, which are exact structural
    properties and pass to machine precision. BAW is an approximation, so a non-zero error
    here is expected and is not evidence of a bug.

    The pass criterion is therefore set on ABSOLUTE dollar error, because the thing we
    ultimately produce is a dollar VaR. Relative error is reported alongside but is NOT
    gated on: it is dominated by cheap options, where a two-cent absolute error is a large
    percentage of a sixty-cent price while being irrelevant to any dollar risk number.
    The largest relative errors in this suite are exactly those cases, and they are called
    out in `check_spy_regime_accuracy`.

    Inputs:
        relative_tolerance (float):  reported only, not gated on. Kept as an argument so
            the reporting threshold is visible at the call site.
        absolute_tolerance (float):  largest acceptable absolute difference, in dollars.
            This is the criterion the test passes or fails on.
        relative_error_price_floor (float): only compute relative error on prices at or
            above this, in dollars.

    Returns:
        bool: True if every case passed the absolute criterion.
    """
    print()
    print("TEST 4: BAW vs 2000-step binomial  (independent convergent reference)")
    print("  BAW paper's STRESS grid: K=100, r=0.08, b=-0.04 (12% dividend yield)")
    print(f"  {'type':>5} {'T':>6} {'sigma':>6} {'S':>7} {'BAW':>11} {'binomial':>11} "
          f"{'abs err':>9} {'rel err':>9} {'early ex':>10}")

    strike = 100.0
    rate = 0.08
    carry = -0.04

    worst_absolute_error = 0.0
    worst_relative_error = 0.0
    n_below_floor = 0

    for option_type in ["call", "put"]:
        for time_to_expiry in [0.25, 0.50]:
            for vol in [0.20, 0.40]:
                for underlying_price in [80.0, 90.0, 100.0, 110.0, 120.0]:
                    approximate = baw_price(underlying_price, strike, time_to_expiry,
                                            rate, carry, vol, option_type)
                    reference = binomial_american_price(underlying_price, strike,
                                                        time_to_expiry, rate, carry, vol,
                                                        option_type)
                    european = black_scholes_price(underlying_price, strike,
                                                   time_to_expiry, rate, carry, vol,
                                                   option_type)

                    absolute_error = abs(approximate - reference)
                    worst_absolute_error = max(worst_absolute_error, absolute_error)

                    if reference >= relative_error_price_floor:
                        relative_error = absolute_error / reference
                        worst_relative_error = max(worst_relative_error, relative_error)
                        relative_display = f"{relative_error:>8.3%}"
                    else:
                        n_below_floor += 1
                        relative_display = f"{'--':>9}"

                    print(f"  {option_type:>5} {time_to_expiry:>6.2f} {vol:>6.2f} "
                          f"{underlying_price:>7.2f} {approximate:>11.6f} "
                          f"{reference:>11.6f} {absolute_error:>9.6f} "
                          f"{relative_display} {approximate - european:>10.6f}")

    passed = worst_absolute_error < absolute_tolerance

    print(f"  Worst absolute error: ${worst_absolute_error:.6f}  "
          f"(tolerance ${absolute_tolerance:.2f}) -> {'PASS' if passed else 'FAIL'}")
    print(f"  Worst relative error: {worst_relative_error:.4%}  "
          f"(reported only, not gated -- reference {relative_tolerance:.2%})")
    print(f"  ({n_below_floor} cases priced below the "
          f"${relative_error_price_floor:.2f} floor were excluded from relative error)")

    return passed


def check_spy_regime_accuracy(absolute_tolerance=0.20, price_floor=0.50):
    """
    Assess BAW accuracy in the parameter regime this project actually operates in.

    TEST 4 stresses the approximation with a 12% dividend yield. Our SPY reference expiry
    has b = 5.5926% against r = 5.4000% -- almost no carry differential at all -- and
    volatilities around 10-25%. That is a much gentler regime, and it is the only one the
    Phase 5 revaluation loop will ever visit, so it deserves its own measurement.

    The result is strongly asymmetric between calls and puts, and that asymmetry is the
    single most useful thing this test tells us:

      - CALLS are essentially EXACT. Since b > r, `baw_price` returns the European price
        with no premium at all, and the lattice agrees to a few tenths of a basis point.
        There is nothing for the approximation to get wrong.
      - PUTS carry real approximation error, because their early-exercise premium is
        genuinely non-zero and BAW does not recover it exactly.

    # NOTE (approximation): BAW's error on puts is NOT a uniform bias, and it does not
    # behave the way one might hope. Measured against a converged lattice at our reference
    # strike, BAW UNDERSTATES the at-the-money put (recovering about 95% of the true
    # early-exercise premium) while OVERSTATING it out of the money -- at K=450 the true
    # premium is $0.0167 and BAW gives $0.0362, more than double. Because the sign of the
    # error changes with moneyness, it does NOT cancel when we take price differences to
    # form scenario P&Ls; it partially amplifies. `check_pnl_error_propagation` measures
    # the consequence directly, and it is around 1% of the P&L. That is acceptable for a
    # methodology replication but it is a real floor on the precision of our VaR, and it
    # is the price of using the filing's specified approximation rather than a lattice.

    Inputs:
        absolute_tolerance (float): largest acceptable absolute difference, in dollars.
            Gated on absolute rather than relative error for the reason given in
            `check_against_binomial`.
        price_floor (float):        only assess options priced at or above this, in
            dollars. Below it a cent of error is a large percentage and tells us nothing.

    Returns:
        bool: True if every case above the price floor passed the absolute criterion.
    """
    print()
    print("TEST 4b: BAW accuracy in the SPY regime  (S=475.31, T=49d, r=5.40%, b=5.59%)")
    print(f"  {'type':>5} {'sigma':>6} {'K':>6} {'BAW':>11} {'binomial':>11} "
          f"{'abs err':>9} {'rel err':>9}")

    underlying_price = 475.31
    time_to_expiry = 0.13424657534246576
    rate = 0.0540
    carry = 0.055926

    worst_by_type = {"call": 0.0, "put": 0.0}
    worst_absolute = 0.0
    n_assessed = 0

    for option_type in ["call", "put"]:
        for vol in [0.09, 0.115, 0.16, 0.25]:
            for strike in [420.0, 450.0, 475.0, 500.0, 540.0]:
                approximate = baw_price(underlying_price, strike, time_to_expiry, rate,
                                        carry, vol, option_type)
                # 4000 steps rather than 2000: this test is the one whose numbers we
                # quote as the project's accuracy claim, so it is worth the extra second.
                reference = binomial_american_price(underlying_price, strike,
                                                    time_to_expiry, rate, carry, vol,
                                                    option_type, n_steps=4000)

                absolute_error = abs(approximate - reference)

                if reference < price_floor:
                    continue

                relative_error = absolute_error / reference
                worst_by_type[option_type] = max(worst_by_type[option_type],
                                                 relative_error)
                worst_absolute = max(worst_absolute, absolute_error)
                n_assessed += 1

                # Print the at-the-money band only, to keep the table readable.
                if strike in (450.0, 475.0, 500.0) and vol in (0.115, 0.25):
                    print(f"  {option_type:>5} {vol:>6.3f} {strike:>6.0f} "
                          f"{approximate:>11.6f} {reference:>11.6f} "
                          f"{absolute_error:>9.6f} {relative_error:>8.3%}")

    print(f"  Cases assessed (priced >= ${price_floor:.2f}): {n_assessed}")
    print(f"  Worst relative error, CALLS: {worst_by_type['call']:.4%}  "
          f"<- b > r, so BAW returns the European price; nothing to get wrong")
    print(f"  Worst relative error, PUTS : {worst_by_type['put']:.4%}  "
          f"<- real early-exercise premium; worst case is an OTM put, see the NOTE")
    print(f"  Worst absolute error       : ${worst_absolute:.6f} "
          f"(tolerance ${absolute_tolerance:.2f})")

    passed = worst_absolute < absolute_tolerance
    print(f"  -> {'PASS' if passed else 'FAIL'}")

    return passed


def check_pnl_error_propagation():
    """
    Measure how much of BAW's pricing error survives into a scenario P&L.

    WHY THIS IS THE NUMBER THAT ACTUALLY MATTERS
    -------------------------------------------
    Phases 5 and 6 never use an option price on its own. They use a DIFFERENCE: the
    scenario price minus today's price. So the question is not "how wrong is the price"
    but "how wrong is the change in the price".

    It would be comfortable to assume the pricing error largely cancels in that
    subtraction, since the same systematic bias sits in both terms. It does not. BAW's
    error varies with spot -- and, worse, changes sign between at-the-money and
    out-of-the-money -- so differencing two prices at two different spots can leave more
    error than either price carried on its own.

    This function measures that directly for our reference put, against a converged
    lattice, across the range of daily moves a 250-day SPY sample actually contains.

    Returns:
        bool: always True. This is a measurement we report, not a property we assert.
    """
    print()
    print("TEST 4c: Does BAW's pricing error cancel in the P&L?  (informational)")
    print("  Reference put: S=475.31, K=475, T=49d, r=5.40%, b=5.59%, sigma=11.5%")

    underlying_price = 475.31
    strike = 475.0
    time_to_expiry = 0.13424657534246576
    rate = 0.0540
    carry = 0.055926
    vol = 0.115

    base_approximate = baw_price(underlying_price, strike, time_to_expiry, rate, carry,
                                 vol, "put")
    base_reference = binomial_american_price(underlying_price, strike, time_to_expiry,
                                             rate, carry, vol, "put", n_steps=8000)
    level_error = base_approximate - base_reference

    print(f"  Base price: BAW {base_approximate:.6f}, lattice {base_reference:.6f}, "
          f"level error {level_error:+.6f}")
    print()
    print(f"  {'shock':>7} {'S':>9} {'BAW P&L':>11} {'true P&L':>11} {'P&L err':>10} "
          f"{'% of P&L':>9}")

    worst_pnl_error = 0.0

    for shock in [-0.06, -0.04, -0.03, -0.02, -0.01, 0.01, 0.02, 0.03, 0.04, 0.06]:
        scenario_price = underlying_price * (1.0 + shock)

        approximate_pnl = baw_price(scenario_price, strike, time_to_expiry, rate, carry,
                                    vol, "put") - base_approximate
        reference_pnl = binomial_american_price(scenario_price, strike, time_to_expiry,
                                                rate, carry, vol, "put",
                                                n_steps=8000) - base_reference

        pnl_error = approximate_pnl - reference_pnl
        worst_pnl_error = max(worst_pnl_error, abs(pnl_error))

        print(f"  {shock:>6.0%} {scenario_price:>9.2f} {approximate_pnl:>11.6f} "
              f"{reference_pnl:>11.6f} {pnl_error:>+10.6f} "
              f"{abs(pnl_error) / abs(reference_pnl):>8.3%}")

    print()
    print(f"  Worst P&L error ${worst_pnl_error:.6f} against a price-level error of "
          f"${abs(level_error):.6f}.")
    print(f"  The error does NOT cancel -- it is {worst_pnl_error / abs(level_error):.1f}x "
          f"LARGER in the P&L than in the price,")
    print(f"  because BAW's error varies with spot and changes sign with moneyness.")
    print(f"  Practical consequence: expect roughly 1% error in scenario P&Ls, and")
    print(f"  therefore in the Phase 6 VaR, purely from using BAW rather than a lattice.")

    return True


def check_monotonicity():
    """
    Prices must move the right way when a single input moves.

      - every option is worth more when volatility rises (more uncertainty is good for
        the holder, who keeps the upside and can walk away from the downside);
      - a call is worth more when the underlying rises;
      - a put is worth less when the underlying rises.

    These catch sign errors that the bound tests in TEST 3 would let through, because a
    price can satisfy every inequality while still responding to its inputs backwards.

    Returns:
        bool: True if every sequence moved in the required direction.
    """
    print()
    print("TEST 5: Monotonicity in volatility and in spot")

    failures = []

    # ---- rising in volatility --------------------------------------------------------
    for option_type in ["call", "put"]:
        for underlying_price in [85.0, 100.0, 115.0]:
            prices = [
                baw_price(underlying_price, 100.0, 0.5, 0.08, -0.04, vol, option_type)
                for vol in [0.10, 0.20, 0.30, 0.45, 0.60]
            ]

            if any(later < earlier for earlier, later in zip(prices, prices[1:])):
                failures.append(f"{option_type} at S={underlying_price} not rising in vol: "
                                f"{[round(p, 6) for p in prices]}")

    print(f"  Rising in volatility          : "
          f"{'PASS' if not failures else 'FAIL'} (6 sequences)")

    # ---- call rising in spot, put falling in spot -------------------------------------
    spot_failures = []

    for vol in [0.15, 0.35]:
        call_prices = [
            baw_price(spot, 100.0, 0.5, 0.08, -0.04, vol, "call")
            for spot in [80.0, 90.0, 100.0, 110.0, 120.0]
        ]
        put_prices = [
            baw_price(spot, 100.0, 0.5, 0.08, -0.04, vol, "put")
            for spot in [80.0, 90.0, 100.0, 110.0, 120.0]
        ]

        if any(later < earlier for earlier, later in zip(call_prices, call_prices[1:])):
            spot_failures.append(f"call at sigma={vol} not rising in spot: {call_prices}")

        if any(later > earlier for earlier, later in zip(put_prices, put_prices[1:])):
            spot_failures.append(f"put at sigma={vol} not falling in spot: {put_prices}")

    print(f"  Call rising / put falling in S: "
          f"{'PASS' if not spot_failures else 'FAIL'} (4 sequences)")

    for failure in (failures + spot_failures)[:5]:
        print(f"    {failure}")

    return not failures and not spot_failures


def check_critical_prices():
    """
    The exercise boundary must sit on the correct side of the strike, and move sensibly.

    For a call the boundary is ABOVE the strike, for a put BELOW it -- see the economics in
    `solve_critical_price`. Both should also move AWAY from the strike as volatility rises:
    the more uncertain the future, the more the remaining optionality is worth, and the
    further the underlying has to travel before giving it up becomes worthwhile.

    Returns:
        bool: True if the boundary was on the right side and moved the right way.
    """
    print()
    print("TEST 6: Critical exercise prices  (K = 100, r = 0.08, b = -0.04, T = 0.5)")
    print(f"  {'sigma':>7} {'S* call':>10} {'S** put':>10}   "
          f"(call boundary must be > 100, put boundary < 100)")

    strike = 100.0
    call_boundaries = []
    put_boundaries = []

    for vol in [0.15, 0.25, 0.40, 0.60]:
        call_boundary = solve_critical_price(strike, 0.5, 0.08, -0.04, vol, "call")
        put_boundary = solve_critical_price(strike, 0.5, 0.08, -0.04, vol, "put")

        call_boundaries.append(call_boundary)
        put_boundaries.append(put_boundary)

        print(f"  {vol:>7.2f} {call_boundary:>10.4f} {put_boundary:>10.4f}")

    side_correct = all(b > strike for b in call_boundaries) and all(
        b < strike for b in put_boundaries
    )

    # Rising vol should push the call boundary up and the put boundary down.
    call_moves_away = all(
        later > earlier for earlier, later in zip(call_boundaries, call_boundaries[1:])
    )
    put_moves_away = all(
        later < earlier for earlier, later in zip(put_boundaries, put_boundaries[1:])
    )

    print(f"  Boundaries on the correct side of the strike : "
          f"{'PASS' if side_correct else 'FAIL'}")
    print(f"  Boundaries move away from strike as vol rises: "
          f"{'PASS' if call_moves_away and put_moves_away else 'FAIL'}")

    return side_correct and call_moves_away and put_moves_away


def check_spy_reference_case():
    """
    Price our actual Phase 0 reference option and show what the American feature is worth.

    Not a pass/fail test -- a reality check that the numbers we will carry into Phases 3
    to 6 are sane, and a demonstration of the point flagged throughout this module: with
    b > r there is no early exercise value in the CALL, while the PUT carries a real one.
    That asymmetry is exactly why the Phase 5 reference option is a put.

    Returns:
        bool: always True. This is informational.
    """
    print()
    print("TEST 7: SPY reference case  (informational, not pass/fail)")
    print("  S=475.31, K=475, T=0.13425 (49 days), r=5.400%, b=5.5926%, sigma=11.5%")
    print(f"  {'type':>5} {'European':>11} {'American':>11} {'early ex':>10} "
          f"{'binomial':>11} {'BAW err':>10}")

    underlying_price = 475.31
    strike = 475.0
    time_to_expiry = 0.13424657534246576
    rate = 0.0540
    carry = 0.055926
    vol = 0.115

    for option_type in ["call", "put"]:
        european = black_scholes_price(underlying_price, strike, time_to_expiry, rate,
                                       carry, vol, option_type)
        american = baw_price(underlying_price, strike, time_to_expiry, rate, carry, vol,
                             option_type)
        reference = binomial_american_price(underlying_price, strike, time_to_expiry,
                                            rate, carry, vol, option_type)

        print(f"  {option_type:>5} {european:>11.6f} {american:>11.6f} "
              f"{american - european:>10.6f} {reference:>11.6f} "
              f"{abs(american - reference):>10.6f}")

    print("  Reading: the call's early-exercise value is exactly zero because b > r --")
    print("  no dividend falls inside this window, so exercising early is never optimal.")
    print("  The put's is positive, which is why the Phase 5 reference option is a put.")

    return True


def _run_standalone():
    """
    Run every validation check and print a single overall verdict.

    Returns:
        None. Prints to stdout. Exits with a non-zero status if any check failed.
    """
    import sys

    print("=" * 86)
    print("BAW PRICING ENGINE -- VALIDATION SUITE")
    print("=" * 86)
    print()

    results = {
        "1. European put-call parity": check_put_call_parity(),
        "2. American call = European when b = r": (
            check_american_call_equals_european_when_no_dividend()
        ),
        "3. American >= European and >= intrinsic": check_american_bounds(),
        "4. BAW vs binomial, stress grid": check_against_binomial(),
        "4b. BAW vs binomial, SPY regime": check_spy_regime_accuracy(),
        "4c. P&L error propagation": check_pnl_error_propagation(),
        "5. Monotonicity": check_monotonicity(),
        "6. Critical exercise prices": check_critical_prices(),
        "7. SPY reference case": check_spy_reference_case(),
    }

    print()
    print("=" * 86)
    print("SUMMARY")
    print("=" * 86)

    for name, passed in results.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")

    all_passed = all(results.values())
    print()
    print(f"  OVERALL: {'ALL CHECKS PASSED' if all_passed else 'ONE OR MORE CHECKS FAILED'}")

    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    _run_standalone()
