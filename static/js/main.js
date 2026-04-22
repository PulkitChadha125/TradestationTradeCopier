// Trade Copier Control
document.addEventListener('DOMContentLoaded', function() {
    const startBtn = document.getElementById('start-copier-btn');
    const stopBtn = document.getElementById('stop-copier-btn');
    const statusText = document.getElementById('status-text');
    
    // Check copier status on load
    checkCopierStatus();
    
    // Check status every 5 seconds
    setInterval(checkCopierStatus, 5000);
    
    if (startBtn) {
        startBtn.addEventListener('click', function() {
            startCopier();
        });
    }
    
    if (stopBtn) {
        stopBtn.addEventListener('click', function() {
            stopCopier();
        });
    }
});

function checkCopierStatus() {
    fetch('/api/copier-status')
        .then(response => response.json())
        .then(data => {
            const startBtn = document.getElementById('start-copier-btn');
            const stopBtn = document.getElementById('stop-copier-btn');
            const statusText = document.getElementById('status-text');
            
            if (data.running) {
                statusText.textContent = 'Trading: Running';
                statusText.style.color = '#28a745';
                if (startBtn) startBtn.style.display = 'none';
                if (stopBtn) stopBtn.style.display = 'inline-block';
            } else {
                statusText.textContent = 'Trading: Stopped';
                statusText.style.color = '#666';
                if (startBtn) startBtn.style.display = 'inline-block';
                if (stopBtn) stopBtn.style.display = 'none';
            }
        })
        .catch(error => {
            console.error('Error checking copier status:', error);
        });
}

function startCopier() {
    fetch('/api/start-trading', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Trading started successfully.');
            checkCopierStatus();
        } else {
            alert('Error starting trading: ' + data.message);
        }
    })
    .catch(error => {
        console.error('Error starting trading:', error);
        alert('Error starting trading: ' + error.message);
    });
}

function stopCopier() {
    if (confirm('Are you sure you want to stop trading and clear sessions?')) {
        fetch('/api/stop-trading', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                alert(data.message || 'Trading stopped.');
                checkCopierStatus();
            } else {
                alert('Error stopping trading: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error stopping trading:', error);
            alert('Error stopping trading: ' + error.message);
        });
    }
}
