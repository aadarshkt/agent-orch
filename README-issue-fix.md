# ao-demo-cart

Demo target for the Agent Orchestrator "Issue → Reviewed Fix" workflow.

The existing tests pass, but the cart has a real bug: the SAVE10 coupon is applied once per line item, so multi-item carts get far more than 10% off. A good fix changes one line and adds a multi-item regression test that fails before the fix and passes after it.

Open this as GitHub issue #1:

> **Bug: SAVE10 gives far more than 10% off on multi-item carts**
> A cart with 3 items got ~30% off with SAVE10. Expected 10% off the subtotal.
