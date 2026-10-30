
document.addEventListener("DOMContentLoaded", function () {
    // Automatically set today's date on the report form.
    const dateInput = document.getElementById("item_date");

    if (dateInput && !dateInput.value) {
        const today = new Date();
        const localDate = new Date(
            today.getTime() - today.getTimezoneOffset() * 60000
        ).toISOString().split("T")[0];

        dateInput.max = localDate;
        dateInput.value = localDate;
    }

    // Confirm before deleting a report.
    document.querySelectorAll(".delete-form").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            if (!confirm("Are you sure you want to delete this report?")) {
                event.preventDefault();
            }
        });
    });

    // Prevent submitting a form multiple times.
    document.querySelectorAll("form").forEach(function (form) {
        form.addEventListener("submit", function () {
            const button = form.querySelector('button[type="submit"]');

            if (button && form.checkValidity()) {
                button.dataset.originalText = button.textContent;
                button.disabled = true;
                button.textContent = "Please wait...";
            }
        });
    });
});
