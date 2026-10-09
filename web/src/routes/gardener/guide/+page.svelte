<svelte:head>
    <title>How Gardener works</title>
    <meta name="robots" content="noindex,nofollow" />
</svelte:head>

<main>
    <nav aria-label="Gardener"><a href="/gardener">← Execution map</a><a href="/gardener?view=work">Work history</a></nav>
    <header><h1>How Gardener works</h1><p>Follow a change request through review, then compare what additional rounds can cost.</p></header>
    <div class="charts">
        <figure>
            <img src="/gardener/review-rounds.png" width="760" height="884" alt="Example with two revisions: you authorize, Phi requests, Prefect queues, and the Exe worker dispatches three sequential coding VMs. Phi reviews each patch and requests changes or approves. One final test VM runs before your merge approval. Four VM runs in total, each deleted after its run." />
            <figcaption>The worker dispatches from heavypad. Phi reviews outside the job VMs. This change-request example includes one final test run; investigations follow a shorter path.</figcaption>
        </figure>
        <figure>
            <img src="/gardener/review-cost.png" width="760" height="978" alt="Illustrative monthly cost for 100 change requests, with a $35 compute pool and assumed model cost of $0.10 per coding-and-review round. No revisions: 200 VM runs and $45. Two revisions: 400 VM runs and $65. Five revisions: 700 VM runs and $95. All work must fit the same pool. Other subscriptions, overages, and retries are excluded." />
            <figcaption>The <a href="https://exe.dev/docs/billing/personal">$35/month Exe pool</a> is a post-trial pricing example, checked October 8, 2026. Model costs are assumptions, not measured charges. VM runs count launches over the month, not simultaneous machines.</figcaption>
        </figure>
    </div>
</main>

<style>
    :global(body) { background: #f2f6fa; }
    main { max-width: 1440px; margin: auto; padding: 24px; color: #172b45; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
    nav { display: flex; gap: 24px; flex-wrap: wrap; }
    a { color: #086acb; text-decoration: underline; text-underline-offset: 3px; }
    nav a { display: inline-flex; min-height: 44px; align-items: center; }
    a:focus-visible { outline: 3px solid #713dce; outline-offset: 4px; }
    header { margin: 24px 0; }
    h1 { font-size: clamp(28px, 3vw, 38px); font-weight: 700; line-height: 1.2; }
    header p { margin-top: 12px; font-size: 18px; line-height: 1.5; max-width: 700px; }
    .charts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; align-items: start; }
    figure { overflow: hidden; border-radius: 12px; background: white; }
    img { display: block; width: 100%; height: auto; }
    figcaption { padding: 8px 28px 28px; font-size: 16px; line-height: 1.6; color: #506176; }
    @media (max-width: 900px) { .charts { grid-template-columns: minmax(0, 1fr); max-width: 760px; margin: auto; } }
    @media (max-width: 480px) { main { padding: 16px 8px; } nav, header { margin-left: 8px; margin-right: 8px; } figcaption { padding: 8px 16px 24px; } }
</style>
