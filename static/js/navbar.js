/**
 * NAVBAR — popover toggle + click-outside-to-close logica
 *
 * Bevat:
 *  - toggleNavPopover(id)     open/sluit popover met gegeven id
 *  - closeAllPopovers()       sluit alle openstaande popovers
 *
 * NB: de functie heet bewust toggleNavPopover (niet togglePopover) om
 * conflict te vermijden met de native HTMLElement.togglePopover() API
 * die sinds 2024 in alle moderne browsers zit (Popover API). Inline
 * onclick="togglePopover(...)" zou de native methode aanroepen i.p.v.
 * onze functie en een NotSupportedError gooien.
 *
 * Patroon: elke .navbar-item heeft optioneel een .navbar-popover als child.
 * Klikken op een navbar-item opent zijn popover. Klikken buiten een popover
 * sluit alles.
 *
 * Afhankelijkheden: geen. Wordt geladen in main bootstrap.
 */

function toggleNavPopover(popoverId) {
    const popover = document.getElementById(popoverId);
    if (!popover) return;

    const wasOpen = popover.classList.contains('show');

    closeAllPopovers();

    if (!wasOpen) {
        popover.classList.add('show');
    }
}

function closeAllPopovers() {
    document.querySelectorAll('.navbar-popover.show').forEach(p => {
        p.classList.remove('show');
    });
}

function registerPopoverOutsideClick() {
    document.addEventListener('click', function(event) {
        if (event.target.closest('.navbar-item')) return;
        closeAllPopovers();
    });
}

// Expose op window
window.toggleNavPopover            = toggleNavPopover;
window.closeAllPopovers            = closeAllPopovers;
window.registerPopoverOutsideClick = registerPopoverOutsideClick;
